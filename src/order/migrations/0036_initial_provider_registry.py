"""Phase 0 migration — adds the (shipping_provider, shipping_kind) pair.

Backwards-compat per the Argo CD PreSync hook deploy model: schema lands
BEFORE the new code rolls.  The columns are nullable / additive so the
old code (still serving traffic at the moment the migration runs) keeps
reading ``shipping_method`` and never touches the new fields.

The RunPython step backfills both new fields from the existing
``shipping_method`` value so rows are immediately consistent — no
""migration tail"" of legacy rows that the Phase 1 code has to special-case.
"""

import django.db.models.deletion
from django.db import migrations, models


def backfill_provider_and_kind(apps, schema_editor):
    Order = apps.get_model("order", "Order")
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")

    boxnow = ShippingProvider.objects.filter(code="boxnow").first()

    Order.objects.filter(shipping_method="home_delivery").update(
        shipping_provider=None,
        shipping_kind="home_delivery",
    )
    if boxnow is not None:
        Order.objects.filter(shipping_method="box_now_locker").update(
            shipping_provider=boxnow,
            shipping_kind="pickup_point",
        )


def reverse_backfill(apps, schema_editor):
    # Forward-only data migration; reversing the schema removes the
    # columns themselves so nothing to undo here.
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("order", "0035_order_shipping_method"),
        ("shipping", "0002_seed_providers"),
    ]

    operations = [
        migrations.AddField(
            model_name="order",
            name="shipping_kind",
            field=models.CharField(
                choices=[
                    ("home_delivery", "Home delivery"),
                    ("pickup_point", "Pickup point / locker"),
                ],
                db_index=True,
                default="home_delivery",
                help_text="Generic fulfilment kind, independent of provider.",
                max_length=32,
                verbose_name="Shipping Kind",
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="shipping_provider",
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    "Carrier handling this order. Null for legacy rows "
                    "where fulfilment is handled outside any registered "
                    "provider."
                ),
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="orders",
                to="shipping.shippingprovider",
                verbose_name="Shipping Provider",
            ),
        ),
        migrations.AlterField(
            model_name="order",
            name="shipping_method",
            field=models.CharField(
                choices=[
                    ("home_delivery", "Home delivery"),
                    ("box_now_locker", "BOX NOW Locker"),
                ],
                db_index=True,
                default="home_delivery",
                help_text=(
                    "Legacy enum kept for backwards compatibility. New "
                    "code reads the (shipping_provider, shipping_kind) "
                    "pair instead — both fields are dual-written by "
                    "OrderService until Phase 3 of the shipping "
                    "abstraction migration."
                ),
                max_length=32,
                verbose_name="Shipping Method",
            ),
        ),
        migrations.RunPython(
            backfill_provider_and_kind, reverse_code=reverse_backfill
        ),
    ]
