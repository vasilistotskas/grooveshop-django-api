"""``Product.vat`` is NOT NULL: release 2 (contract) of the VAT rate rule.

Release 1 (0068 backfill, 0069 ``PROTECT`` and required on every write path)
shipped as v3.234.0 and runs in production, so no pod that can still INSERT a
product without a rate is left. Only now may the column be tightened; doing it
in 0069 would have broken the release still serving during that rollout.

REFUSES to run while any product holds a NULL rate, in this schema, soft
deleted rows included, and names them (id and SKU, the first ``LISTED``).
Nothing is guessed: a rate is a tax decision, so an operator sets it (0068
gave every product it found the standard rate). The refusal
comes before the ``ALTER``, inside the same transaction, so the PreSync hook
stops and the serving pods keep their schema.

Reverse drops the NOT NULL again.
"""

from django.db import migrations, models
from django.db.models import deletion

TABLE = "product_product"
# How many of the products without a rate the refusal names.
LISTED = 20


def refuse_a_product_without_a_rate(apps, schema_editor):
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT count(*) FROM {TABLE} WHERE vat_id IS NULL")
        (missing,) = cursor.fetchone()
        if not missing:
            return
        cursor.execute(
            f"SELECT id, sku FROM {TABLE} WHERE vat_id IS NULL "
            "ORDER BY id LIMIT %s",
            [LISTED],
        )
        named = ", ".join(f"{pk} ({sku})" for pk, sku in cursor.fetchall())
    schema = getattr(connection, "schema_name", "public")
    more = f" and {missing - LISTED} more" if missing > LISTED else ""
    raise RuntimeError(
        f"Schema {schema!r} has {missing} product(s) without a VAT rate "
        "(soft-deleted ones included), so product.vat cannot become NOT "
        f"NULL: id (sku) {named}{more}. Set their rate in the admin, then "
        "migrate again. Nothing was changed."
    )


class Migration(migrations.Migration):
    contract_of = [("product", "0069_product_vat_required")]

    dependencies = [
        ("product", "0069_product_vat_required"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterField(
                    model_name="product",
                    name="vat",
                    field=models.ForeignKey(
                        on_delete=deletion.PROTECT,
                        related_name="products",
                        to="vat.vat",
                        verbose_name="VAT",
                    ),
                ),
            ],
            database_operations=[
                migrations.RunPython(
                    refuse_a_product_without_a_rate,
                    migrations.RunPython.noop,
                    elidable=False,
                ),
                migrations.RunSQL(
                    f"ALTER TABLE {TABLE} ALTER COLUMN vat_id SET NOT NULL",
                    reverse_sql=(
                        f"ALTER TABLE {TABLE} ALTER COLUMN vat_id DROP NOT NULL"
                    ),
                ),
            ],
        ),
    ]
