"""Carry the shipping and payment fee an existing invoice was built from.

The PDF template renders those rows behind ``{% if %}`` and prints the
total unconditionally, so a row left at zero produces a document whose
total contains a shipping and a fee that have no lines — on a document
already registered with AADE, that is a legally filed total its own
lines do not add up to.

Split from the ADD COLUMN migration on purpose. ``ADD COLUMN`` takes
ACCESS EXCLUSIVE on ``order_invoice``, and sharing a transaction with the
backfill would hold that lock for the whole scan. ``order_invoice`` is on
the customer order path (``get_has_invoice`` in the list AND detail
serializers), and every connection carries ``statement_timeout=30s``, so
blocked reads on the pods still running the old image would not wait —
they would 500. Here the lock is held per batch instead.

Idempotent: every value is re-derived from the invoice and its order, so
a partially applied run is safe to replay, which is what makes
``atomic = False`` acceptable.
"""

from decimal import Decimal

from django.db import migrations, transaction

BATCH = 500
ZERO = Decimal("0")


def _amount(money):
    return Decimal(str(getattr(money, "amount", money) or 0))


def backfill_components(apps, schema_editor):
    Invoice = apps.get_model("order", "Invoice")

    fields = [
        "shipping",
        "shipping_currency",
        "payment_fee",
        "payment_fee_currency",
    ]

    # Page by primary key rather than ``.iterator()``. A server-side
    # cursor is closed by COMMIT, and this migration commits once per
    # batch, so iterating one would die with InvalidCursorName partway
    # through. Each page is its own query, which also makes a resumed
    # run cheap.
    last_pk = 0
    while True:
        page = list(
            Invoice.objects.select_related("order")
            .filter(pk__gt=last_pk)
            .order_by("pk")
            # Every money field needs its companion currency column
            # loaded too — django-money builds the Money from the pair,
            # and a deferred currency raises KeyError on attribute
            # access.
            .only(
                "subtotal",
                "subtotal_currency",
                "total_vat",
                "total_vat_currency",
                "total",
                "total_currency",
                "shipping",
                "shipping_currency",
                "payment_fee",
                "payment_fee_currency",
                "order__payment_method_fee",
                "order__payment_method_fee_currency",
            )[:BATCH]
        )
        if not page:
            return
        last_pk = page[-1].pk

        for invoice in page:
            order = invoice.order

            # The frozen row already carries the authoritative answer:
            # _order_totals guarantees total == subtotal + vat +
            # shipping + fee, so whatever is left over IS the two
            # components. Deriving the split from the order's CURRENT
            # values could contradict a total frozen at issue time —
            # relocating the very defect this exists to fix onto a
            # registered document. Take the fee from the order only as
            # far as the residual allows and let shipping absorb the
            # rest, so the printed lines always sum to the printed
            # total.
            residual = (
                _amount(invoice.total)
                - _amount(invoice.subtotal)
                - _amount(invoice.total_vat)
            )
            if residual <= ZERO:
                fee = ZERO
                shipping = ZERO
            else:
                fee = min(_amount(order.payment_method_fee), residual)
                shipping = residual - fee

            invoice.shipping = shipping
            invoice.payment_fee = fee

        with transaction.atomic():
            Invoice.objects.bulk_update(page, fields)


class Migration(migrations.Migration):
    # Each batch commits on its own; see the module docstring.
    atomic = False

    dependencies = [
        ("order", "0049_invoice_total_components"),
    ]

    operations = [
        migrations.RunPython(backfill_components, migrations.RunPython.noop),
    ]
