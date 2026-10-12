"""Give every existing order line the VAT rate its product carries today.

``OrderItem.vat_rate`` (0064) is the rate a line was sold at, written at
checkout from now on. Lines that predate it were never recorded, so the
product's current VAT is the best information there is: it is exactly
what the invoice and the myDATA payload read before the snapshot existed,
so no document changes for an order whose product VAT was left alone.

Split from the ADD COLUMN migration on purpose, like ``0050``: the column
arrives with ``db_default=0`` (the previous release still INSERTs without
it, and the rows are valid at 0 until this runs). A product with no VAT
row keeps 0, which is what it was invoiced at.

One UPDATE: it takes row locks only, so the pods still on the old image
keep reading and writing order lines, and it is idempotent.
"""

from django.db import migrations
from django.db.models import OuterRef, Subquery


def backfill_vat_rate(apps, schema_editor):
    OrderItem = apps.get_model("order", "OrderItem")
    Product = apps.get_model("product", "Product")

    # ``_base_manager``: a soft-deleted product still carries the VAT its
    # lines were invoiced at, and the historical default manager of a
    # soft-delete model may hide it.
    current_rate = Product._base_manager.filter(pk=OuterRef("product_id")).values(
        "vat__value"
    )[:1]
    OrderItem._base_manager.filter(product__vat__isnull=False).update(
        vat_rate=Subquery(current_rate)
    )


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0064_orderitem_vat_rate"),
    ]

    operations = [
        migrations.RunPython(backfill_vat_rate, migrations.RunPython.noop),
    ]
