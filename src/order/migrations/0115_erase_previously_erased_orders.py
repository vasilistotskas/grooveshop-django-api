"""Finish the erasure of orders erased before ``order.privacy`` existed.

The erasure before it blanked an order's columns but left copies of the
buyer's data: ``metadata["meta"]`` (IP address, user agent, Meta's browser
and click ids), the typed cancel reason and its copies in the stock log,
the refund attempts and the ledger, the "email sent to <address>" notes of
the order's history, and every carrier record. Those orders carry the old
placeholder email (``deleted-<hex>@deleted.invalid``) and no
``personal_data_erased_at``.

This clears what the order side can clear from what is left on the order,
and stamps ``personal_data_erased_at``: the next daily sweep
(``user.tasks.erase_late_carrier_data``) then clears the carriers' records
of it, as for any order erased that day. The buyer's email, name and phone
are gone from those orders, so a history note is cleared of any email
address it names. Only what is still on the order can be found: a
soft-deleted order the old erasure skipped was unlinked from its account
with its data and cannot be told apart from a guest's.

Forward only: an erasure is not undone. Historical models only; the
helpers it needs are frozen copies here. Batches of 500, each stamped as
it goes, so a run stopped halfway picks up where it left off.
"""

import re

from django.db import migrations
from django.utils import timezone

# Frozen copies, as this migration was written, of what the code it
# completes uses: ``Order.PERSONAL_METADATA``, ``Order.BILLING_FIELDS`` and
# ``core.utils.redaction``. A migration must not change when that code does.
PERSONAL_METADATA = (("meta",), ("cancellation", "reason"))
BILLING_FIELDS = (
    "billing_vat_id",
    "billing_country",
    "billing_company_name",
    "billing_tax_office",
    "billing_activity",
    "billing_street",
    "billing_street_number",
    "billing_city",
    "billing_zipcode",
)
REDACTED = "[deleted]"
_MIN_TEXT = 6
_MIN_CUT_TEXT = 32
_BATCH = 500
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _without(value, text):
    if text in value:
        return value.replace(text, REDACTED)
    for length in range(len(text) - 1, _MIN_CUT_TEXT - 1, -1):
        if value.endswith(text[:length]):
            return value[:-length] + REDACTED
    return value


def _redact(value, texts):
    """*value* (a string, or JSON holding strings) with each of *texts*
    and every email address replaced, also where a column cut one."""
    if isinstance(value, dict):
        return {key: _redact(item, texts) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, texts) for item in value]
    if not isinstance(value, str):
        return value
    for text in texts:
        value = _without(value, text)
    return _EMAIL.sub(REDACTED, value)


def _drop_metadata(metadata):
    for *parents, leaf in PERSONAL_METADATA:
        node = metadata
        for key in parents:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(leaf, None)


def finish_erasure(apps, schema_editor):
    """Idempotent: an order it finishes is stamped and never matched again.
    A store with no such order (every new store, which provisioning
    migrates from empty) costs one query that finds nothing."""
    Order = apps.get_model("order", "Order")
    OrderHistory = apps.get_model("order", "OrderHistory")
    StockLog = apps.get_model("order", "StockLog")
    RefundAttempt = apps.get_model("order", "RefundAttempt")
    PaymentTransaction = apps.get_model("order", "PaymentTransaction")

    pending = Order._base_manager.filter(
        email__startswith="deleted-",
        email__endswith="@deleted.invalid",
        personal_data_erased_at__isnull=True,
    ).order_by("pk")
    now = timezone.now()
    while True:
        batch = list(
            pending.values(
                "pk",
                "metadata",
                "document_type",
                "status",
                "timologio_invoice_mark",
                "timologio_invoice_number",
            )[:_BATCH]
        )
        if not batch:
            return
        for order in batch:
            _finish(
                order,
                now,
                OrderHistory,
                StockLog,
                RefundAttempt,
                PaymentTransaction,
                Order,
            )


def _finish(
    order, now, OrderHistory, StockLog, RefundAttempt, PaymentTransaction, Order
):
    metadata = order["metadata"] if isinstance(order["metadata"], dict) else {}
    cancellation = metadata.get("cancellation")
    reason = (
        cancellation.get("reason") if isinstance(cancellation, dict) else None
    )
    texts = (
        [reason] if isinstance(reason, str) and len(reason) >= _MIN_TEXT else []
    )
    for model, columns in (
        (StockLog, ("reason",)),
        (RefundAttempt, ("reason",)),
        (PaymentTransaction, ("description",)),
        (OrderHistory, ("new_value", "previous_value")),
    ):
        for row in model._base_manager.filter(order_id=order["pk"]).values(
            "pk", *columns
        ):
            update = {}
            for column in columns:
                redacted = _redact(row[column], texts)
                if redacted != row[column]:
                    update[column] = redacted
            if update:
                model._base_manager.filter(pk=row["pk"]).update(**update)

    _drop_metadata(metadata)
    update = {"metadata": metadata, "personal_data_erased_at": now}
    keeps_billing = (
        order["timologio_invoice_mark"] is not None
        or order["timologio_invoice_number"]
        or (
            order["document_type"] == "INVOICE"
            and order["status"] != "CANCELED"
        )
    )
    if not keeps_billing:
        update.update(dict.fromkeys(BILLING_FIELDS, ""))
    Order._base_manager.filter(pk=order["pk"]).update(**update)


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0114_order_personal_data_erased_at"),
    ]

    operations = [
        migrations.RunPython(finish_erasure, migrations.RunPython.noop),
    ]
