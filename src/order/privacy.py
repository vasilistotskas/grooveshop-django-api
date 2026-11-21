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
    counts["orders_anonymised"] = orders.update(user=None, **erased)
    _clear_and_stamp(order_ids)
    return counts


def _clear_and_stamp(order_ids: list[int]) -> None:
    """What every erased order ends with: the billing fields cleared unless
    an invoice needs them (:func:`keeps_billing`), ``PERSONAL_METADATA``
    dropped, and the ``personal_data_erased_at`` stamp the daily sweep
    reads."""
    from order.models.order import Order

    orders = Order.objects.all_with_deleted().filter(id__in=order_ids)
    orders.exclude(keeps_billing()).update(
        **dict.fromkeys(Order.BILLING_FIELDS, "")
    )
    for order_id, metadata in orders.values_list("id", "metadata"):
        if drop_paths(metadata, Order.PERSONAL_METADATA):
            Order.objects.all_with_deleted().filter(id=order_id).update(
                metadata=metadata
            )
    orders.update(personal_data_erased_at=timezone.now())


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
    """Clear the carrier data that arrived for an order after its erasure,
    and finish the orders an earlier erasure left (:func:`finish_earlier_erasures`).

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

    finished = finish_earlier_erasures()
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
    counts = erase_carrier_data(sorted(swept), buyer_values={})
    counts["earlier_erasures_finished"] = finished
    return counts


def finish_earlier_erasures() -> int:
    """Finish the orders an erasure from before this module left: it
    blanked the order's columns, with the placeholder email
    (``deleted-<hex>@deleted.invalid``), and kept every copy (the browser
    identity and cancel reason in ``metadata``, the reason's copies, the
    "email sent to <address>" history notes, the carriers' records).

    Their copies are redacted by what is left on the order (the cancel
    reason), and their history by every email address it names, as the
    buyer's is gone; then they get what every erased order ends with
    (:func:`_clear_and_stamp`). Stamped today, the sweep clears their
    carrier records in the same run. Idempotent: a stamped order is never
    matched again. Found through the email's trigram index
    (``order_email_search_ix``). A soft-deleted order that erasure skipped
    was unlinked from its account with its data and cannot be told apart
    from a guest's."""
    from order.models.order import Order

    order_ids = list(
        Order.objects.all_with_deleted()
        .filter(
            user__isnull=True,
            email__startswith="deleted-",
            email__endswith="@deleted.invalid",
            personal_data_erased_at__isnull=True,
        )
        .values_list("id", flat=True)
    )
    if not order_ids:
        return 0
    _erase_copies(_buyer_values(order_ids), emails=True)
    _clear_and_stamp(order_ids)
    return len(order_ids)


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


def _erase_copies(values: dict[int, list[str]], *, emails: bool = False) -> int:
    """Remove the buyer's values from the free text the order's own records
    copy them into: its history (``OrderHistory``: "email sent to <their
    address>", a refund's reason), the stock log of restocked lines, the
    refund attempts and the ledger rows. The carriers' copies are in their
    own records; the order's own are its ``PERSONAL_FIELDS`` and
    ``PERSONAL_METADATA``. With *emails*, every email address in them goes
    too, for an order whose buyer's address is already gone."""
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
                redacted = redact(value, values[row.order_id], emails=emails)
                if redacted != value:
                    update[column] = redacted
            if update:
                model.objects.filter(pk=row.pk).update(**update)
                changed += 1
    return changed
