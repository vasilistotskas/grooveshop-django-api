"""An order's personal data: what a data export lists and GDPR erasure
clears (``user.services.gdpr``).

One definition, on the model: ``Order.PERSONAL_FIELDS`` and
``Order.PERSONAL_METADATA``, plus each carrier's own records of the order
(``ShippingCarrierInterface.erase_recipient_data`` / ``recipient_data``).
Soft-deleted orders are the subject's too, so every query here reads
``all_with_deleted``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from core.utils.json_paths import drop_paths, pick_paths
from core.utils.redaction import redact, redaction_texts
from shipping.interfaces import registered_providers

PLACEHOLDER_NAME = "[deleted]"


def keeps_billing() -> Q:
    """Orders whose billing fields (``Order.BILLING_FIELDS``) erasure keeps:
    an invoice was issued in AADE's timologio app (its MARK or number is
    recorded), or one is owed: an INVOICE order that was not canceled,
    which staff issue by hand from those fields. Any other order has no
    document that needs them (a retail receipt keeps its own
    ``buyer_snapshot``)."""
    from order.enum.document_type import OrderDocumentTypeEnum
    from order.enum.status import OrderStatus

    return (
        Q(timologio_invoice_mark__isnull=False)
        | ~Q(timologio_invoice_number="")
        | (
            Q(document_type=OrderDocumentTypeEnum.INVOICE)
            & ~Q(status=OrderStatus.CANCELED)
        )
    )


def export_order(order: Any) -> dict[str, Any]:
    """The personal part of *order* in a data export: every field in
    ``PERSONAL_FIELDS`` and the ``PERSONAL_METADATA`` it holds."""
    from order.models.order import Order

    return {
        **{field: getattr(order, field) for field in Order.PERSONAL_FIELDS},
        "phone": str(order.phone) if order.phone else None,
        "metadata": pick_paths(order.metadata, Order.PERSONAL_METADATA),
    }


def carrier_data(order_ids: list[int]) -> dict[int, dict]:
    """What the carriers reported about each order's recipient, for a data
    export, by order id."""
    data: dict[int, dict] = {}
    for carrier in registered_providers():
        for order_id, entry in carrier.recipient_data(order_ids).items():
            data.setdefault(order_id, {}).update(entry)
    return data


def erase_orders(
    order_ids: list[int], *, placeholder_email: str
) -> dict[str, int]:
    """Clear the buyer's personal data from the given orders and every
    copy of it, and stamp them ``personal_data_erased_at``. The email is
    replaced with *placeholder_email*, the one the rest of the subject's
    records get.

    The buyer's values (email, name, phone, the typed cancel reason) are
    read first, while the order holds them: the copies in free text are
    found by them.
    """
    from order.models.order import Order

    if not order_ids:
        return {"orders_anonymised": 0, "carrier_pii_scrubbed": 0}
    values = _buyer_values(order_ids)
    counts = erase_carrier_data(order_ids, buyer_values=values)
    counts["copies_redacted"] = _erase_copies(values)

    placeholders = {
        "first_name": PLACEHOLDER_NAME,
        "last_name": PLACEHOLDER_NAME,
        "email": placeholder_email,
        "street": PLACEHOLDER_NAME,
        "city": PLACEHOLDER_NAME,
    }
    erased = {
        field: placeholders.get(field, "")
        for field in Order.PERSONAL_FIELDS
        if field not in Order.BILLING_FIELDS
    }
    orders = Order.objects.all_with_deleted().filter(id__in=order_ids)
    now = timezone.now()
    counts["orders_anonymised"] = orders.update(
        user=None, personal_data_erased_at=now, **erased
    )
    orders.exclude(keeps_billing()).update(
        **dict.fromkeys(Order.BILLING_FIELDS, "")
    )
    for order_id, metadata in orders.values_list("id", "metadata"):
        if drop_paths(metadata, Order.PERSONAL_METADATA):
            Order.objects.all_with_deleted().filter(id=order_id).update(
                metadata=metadata
            )
    return counts


def erase_carrier_data(
    order_ids: list[int], *, buyer_values: dict[int, list[str]]
) -> dict[str, int]:
    """Every carrier's records of the given orders, cleared of the
    recipient; *buyer_values* are what to redact from the free text staff
    wrote on them, by order. Also what clears the carrier data that
    reaches an order after its erasure (``erase_late_carrier_data``), when
    those values are gone."""
    counts = {"carrier_pii_scrubbed": 0}
    if not order_ids:
        return counts
    for carrier in registered_providers():
        for kind, rows in carrier.erase_recipient_data(
            order_ids, buyer_values=buyer_values
        ).items():
            counts[kind] = counts.get(kind, 0) + rows
    return counts


def erase_late_carrier_data() -> dict[str, int]:
    """Clear the carrier data that arrived for an order after its erasure.

    A parcel still moving when its buyer is erased goes on being tracked,
    delivered and paid out: each poll, webhook and COD answer writes the
    recipient's details again, the last one (who took delivery, the COD
    receiver) included. An erased order is swept again when it was erased,
    or a carrier wrote a record of it (``orders_written_since``), within
    the last ``ERASURE_CARRIER_SWEEP_LOOKBACK_DAYS``: longer than any gap
    between two daily runs, so no write is missed, and an order nothing
    is written about is not read again.
    """
    from order.models.order import Order

    since = timezone.now() - timedelta(
        days=settings.ERASURE_CARRIER_SWEEP_LOOKBACK_DAYS
    )
    erased = Order.objects.all_with_deleted().filter(
        personal_data_erased_at__isnull=False
    )
    swept = set(
        erased.filter(personal_data_erased_at__gte=since).values_list(
            "id", flat=True
        )
    )
    older = list(
        erased.filter(personal_data_erased_at__lt=since).values_list(
            "id", flat=True
        )
    )
    if older:
        for carrier in registered_providers():
            swept |= carrier.orders_written_since(older, since)
    return erase_carrier_data(sorted(swept), buyer_values={})


def _buyer_values(order_ids: list[int]) -> dict[int, list[str]]:
    """Each order's email, name, phone (also its national ten digits, as a
    carrier or a note writes it) and typed cancel reason, the texts its
    copies are found by."""
    from order.models.order import Order

    values: dict[int, list[str]] = {}
    for order_id, email, first, last, phone, reason in (
        Order.objects.all_with_deleted()
        .filter(id__in=order_ids)
        .values_list(
            "id",
            "email",
            "first_name",
            "last_name",
            "phone",
            "metadata__cancellation__reason",
        )
    ):
        texts = redaction_texts(
            reason if isinstance(reason, str) else "",
            email,
            f"{first} {last}".strip(),
            str(phone or ""),
            str(phone or "")[-10:],
        )
        if texts:
            values[order_id] = texts
    return values


def _erase_copies(values: dict[int, list[str]]) -> int:
    """Remove the buyer's values from the free text the order's own records
    copy them into: its history (``OrderHistory``: "email sent to <their
    address>", a refund's reason), the stock log of restocked lines, the
    refund attempts and the ledger rows. The carriers' copies are in their
    own records; the order's own are its ``PERSONAL_FIELDS`` and
    ``PERSONAL_METADATA``."""
    from order.models.history import OrderHistory
    from order.models.payment_transaction import PaymentTransaction
    from order.models.refund_attempt import RefundAttempt
    from order.models.stock_log import StockLog

    changed = 0
    for model, columns in (
        (OrderHistory, ("new_value", "previous_value")),
        (StockLog, ("reason",)),
        (RefundAttempt, ("reason",)),
        (PaymentTransaction, ("description",)),
    ):
        for row in model.objects.filter(order_id__in=values):
            update = {}
            for column in columns:
                value = getattr(row, column)
                redacted = redact(value, values[row.order_id])
                if redacted != value:
                    update[column] = redacted
            if update:
                model.objects.filter(pk=row.pk).update(**update)
                changed += 1
    return changed
