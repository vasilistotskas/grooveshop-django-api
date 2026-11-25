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

3. The old unique ``(provider, country, kind)`` and its index go, and the
   column is emptied. Nothing keeps it true any more: a zone given regions
   would keep its rate's old country, which the unique would hold against
   a new base zone's rate (the release still serving, or back in a
   rollback, writes ``country``), and a stale foreign key would keep a
   country from being deleted. That release reads neither: it prices by
   the zone, and its state has no such unique (``0025``).

The contract that drops the rate's column is ``contract_of`` this
migration. The reverse fills the column from the zone as the release
before wrote it (a base zone's country, none for a region zone's rate).
"""

from django.db import migrations


class Migration(migrations.Migration):
    contract_of = [("shipping", "0025_shipping_rate_zone_required")]

    dependencies = [
        ("shipping", "0026_shipping_rate_zone_rerun"),
    ]

    operations = [
        migrations.RunSQL(
            [
                "ALTER TABLE shipping_shippingrate "
                "ALTER COLUMN zone_id SET NOT NULL",
                "ALTER TABLE shipping_shippingrate DROP CONSTRAINT IF EXISTS "
                "shipping_rate_provider_country_kind_unique",
                "DROP INDEX IF EXISTS shipping_rate_country_active_ix",
                "UPDATE shipping_shippingrate SET country_id = NULL "
                "WHERE country_id IS NOT NULL",
            ],
            reverse_sql=[
                "UPDATE shipping_shippingrate AS rate "
                "SET country_id = zone.country_id "
                "FROM shipping_shippingzone AS zone "
                "WHERE zone.id = rate.zone_id AND NOT EXISTS ("
                "SELECT 1 FROM shipping_shippingzoneregion AS held "
                "WHERE held.zone_id = zone.id)",
                # The rows just written have queued foreign-key checks,
                # which an ALTER of the table refuses.
                "SET CONSTRAINTS ALL IMMEDIATE",
                "SET CONSTRAINTS ALL DEFERRED",
                "CREATE INDEX shipping_rate_country_active_ix "
                "ON shipping_shippingrate (country_id, is_active)",
                "ALTER TABLE shipping_shippingrate ADD CONSTRAINT "
                "shipping_rate_provider_country_kind_unique "
                "UNIQUE (provider_id, country_id, kind)",
                "ALTER TABLE shipping_shippingrate "
                "ALTER COLUMN zone_id DROP NOT NULL",
            ],
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
