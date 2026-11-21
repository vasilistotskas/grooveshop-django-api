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
from shipping.interfaces import registered_providers

PLACEHOLDER_NAME = "[deleted]"
# Replaces the buyer's values in the free text of the order's records.
REDACTED = "[deleted]"
# Shorter values (a two-letter name) would match unrelated text.
_MIN_COPIED_VALUE = 6
# A copy cut at its column's length ends with the start of the value; one
# this short is never cut there, so it is only matched whole.
_MIN_CUT_TEXT = 32

# An order whose invoice was issued (in AADE's timologio app) keeps the
# invoice's counterparty with it; any other order has no document that
# needs the billing fields.
ISSUED_INVOICE = Q(timologio_invoice_mark__isnull=False) | ~Q(
    timologio_invoice_number=""
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

    The carriers go first: they look for the recipient's name and phone in
    their call history, and those are still on the order.
    """
    from order.models.order import Order

    counts = erase_carrier_data(order_ids)
    if not order_ids:
        return {"orders_anonymised": 0, **counts}
    counts["copies_redacted"] = _erase_copies(order_ids)

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
    orders.exclude(ISSUED_INVOICE).update(
        **dict.fromkeys(Order.BILLING_FIELDS, "")
    )
    for order_id, metadata in orders.values_list("id", "metadata"):
        if drop_paths(metadata, Order.PERSONAL_METADATA):
            Order.objects.all_with_deleted().filter(id=order_id).update(
                metadata=metadata
            )
    return counts


def erase_carrier_data(order_ids: list[int]) -> dict[str, int]:
    """Every carrier's records of the given orders, cleared of the
    recipient. Also what clears the carrier data that reaches an order
    after its erasure (``erase_late_carrier_data``)."""
    counts = {"carrier_pii_scrubbed": 0}
    if not order_ids:
        return counts
    for carrier in registered_providers():
        for kind, rows in carrier.erase_recipient_data(order_ids).items():
            counts[kind] = counts.get(kind, 0) + rows
    return counts


def erase_late_carrier_data() -> dict[str, int]:
    """Clear the carrier data that arrived for an order after its erasure.

    A parcel still moving when its buyer is erased goes on being tracked,
    delivered and paid out: each poll, webhook and COD answer writes the
    recipient's details again. An erased order is swept again while one
    of its parcels is in motion (``orders_in_motion``: not in a terminal
    state, or delivered and waiting on its COD payout), and in any case for
    ``ERASURE_CARRIER_FOLLOW_UP_DAYS`` after the erasure, for what a carrier
    reports about a parcel that has settled.
    """
    from order.models.order import Order

    erased = Order.objects.all_with_deleted().filter(
        personal_data_erased_at__isnull=False
    )
    since = timezone.now() - timedelta(
        days=settings.ERASURE_CARRIER_FOLLOW_UP_DAYS
    )
    recent = set(
        erased.filter(personal_data_erased_at__gte=since).values_list(
            "id", flat=True
        )
    )
    older = list(
        erased.filter(personal_data_erased_at__lt=since).values_list(
            "id", flat=True
        )
    )
    moving: set[int] = set()
    if older:
        for carrier in registered_providers():
            moving |= carrier.orders_in_motion(older)
    return erase_carrier_data(sorted(recent | moving))


def _erase_copies(order_ids: list[int]) -> int:
    """Remove the buyer's values from the free text the order's own records
    copy them into: its history (``OrderHistory``: "email sent to <their
    address>", a refund's reason), the stock log of restocked lines, the
    refund attempts and the ledger rows. The values are the order's email,
    name and phone and its typed cancel reason, read before the order is
    cleared. The carriers' copies are in their own records; the order's
    own are its ``PERSONAL_FIELDS`` and ``PERSONAL_METADATA``."""
    from order.models.history import OrderHistory
    from order.models.order import Order
    from order.models.payment_transaction import PaymentTransaction
    from order.models.refund_attempt import RefundAttempt
    from order.models.stock_log import StockLog

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
        texts = [
            str(text)
            for text in (
                reason if isinstance(reason, str) else "",
                email,
                f"{first} {last}".strip(),
                str(phone or ""),
                # The national number, as a carrier or a note writes it.
                str(phone or "")[-10:],
            )
            if text and len(str(text)) >= _MIN_COPIED_VALUE
        ]
        if texts:
            # Longest first: a name inside a reason goes with the reason.
            values[order_id] = sorted(texts, key=len, reverse=True)

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
                redacted = _redact(value, values[row.order_id])
                if redacted != value:
                    update[column] = redacted
            if update:
                model.objects.filter(pk=row.pk).update(**update)
                changed += 1
    return changed


def _redact(value: Any, texts: list[str]) -> Any:
    """*value* (a string, or JSON holding strings) with each of *texts*
    replaced, also where a column's length cut one short."""
    if isinstance(value, dict):
        return {key: _redact(item, texts) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, texts) for item in value]
    if not isinstance(value, str):
        return value
    for text in texts:
        value = _without(value, text)
    return value


def _without(value: str, text: str) -> str:
    """*value* with *text* replaced, also where a column's length cut it."""
    if text in value:
        return value.replace(text, REDACTED)
    for length in range(len(text) - 1, _MIN_CUT_TEXT - 1, -1):
        if value.endswith(text[:length]):
            return value[:-length] + REDACTED
    return value
