"""A rate's zone is NOT NULL, and ``country`` leaves the code.

Second of the three releases that move ``ShippingRate.country`` onto
``ShippingRate.zone`` (``docs/order/shipping-zones.md``):

1. ``zone`` becomes NOT NULL in the database, as the state already says
   (``0025``): the release before this one writes it on every rate, and
   ``0026`` has zoned what the release before that wrote.
2. ``country`` leaves the state only, on the rate and its history. The
   release still serving names the column in every SELECT and INSERT, so
   the column stays until the release after this one. It is nullable, so
   this release's INSERTs need not name it.

The contract that drops the rate's column is ``contract_of`` this
migration. The old unique ``(provider, country, kind)`` and its index go
with the column; meanwhile they never refuse a rate this release writes,
whose ``country`` is NULL.
"""

from django.db import migrations


class Migration(migrations.Migration):
    contract_of = [("shipping", "0025_shipping_rate_zone_required")]

    dependencies = [
        ("shipping", "0026_shipping_rate_zone_rerun"),
    ]

    operations = [
        migrations.RunSQL(
            "ALTER TABLE shipping_shippingrate ALTER COLUMN zone_id SET NOT NULL",
            reverse_sql=(
                "ALTER TABLE shipping_shippingrate "
                "ALTER COLUMN zone_id DROP NOT NULL"
            ),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(
                    model_name="shippingrate", name="country"
                ),
                migrations.RemoveField(
                    model_name="historicalshippingrate", name="country"
                ),
            ],
        ),
    ]
