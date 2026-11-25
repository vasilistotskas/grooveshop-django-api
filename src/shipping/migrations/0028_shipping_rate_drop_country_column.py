"""Drop ``shipping_shippingrate.country_id``: the contract of ``0027``.

The release that shipped ``0027`` neither reads nor writes the column, and
it is what runs when this one migrates. The old unique
``(provider, country, kind)`` and the old index go with it. The history
table keeps its column: a history row whose country never had a base zone
has only that to say where it applied.

The reverse adds the column back nullable, with its foreign key, index and
unique, as ``0027`` left them, and fills it from the zone as release N wrote
it: a base zone's country, none for a region zone's rate.
"""

from django.db import migrations


class Migration(migrations.Migration):
    contract_of = [("shipping", "0027_shipping_rate_country_out_of_state")]

    dependencies = [
        ("shipping", "0027_shipping_rate_country_out_of_state"),
    ]

    operations = [
        migrations.RunSQL(
            "ALTER TABLE shipping_shippingrate DROP COLUMN IF EXISTS country_id",
            reverse_sql=[
                (
                    "ALTER TABLE shipping_shippingrate ADD COLUMN country_id "
                    "varchar(2) NULL CONSTRAINT "
                    "shipping_shippingrate_country_id_fk REFERENCES "
                    "country_country (alpha_2) DEFERRABLE INITIALLY DEFERRED"
                ),
                (
                    "UPDATE shipping_shippingrate AS rate "
                    "SET country_id = zone.country_id "
                    "FROM shipping_shippingzone AS zone "
                    "WHERE zone.id = rate.zone_id AND NOT EXISTS ("
                    "SELECT 1 FROM shipping_shippingzoneregion AS held "
                    "WHERE held.zone_id = zone.id)"
                ),
                "SET CONSTRAINTS ALL IMMEDIATE",
                "SET CONSTRAINTS ALL DEFERRED",
                (
                    "ALTER TABLE shipping_shippingrate ADD CONSTRAINT "
                    "shipping_rate_provider_country_kind_unique "
                    "UNIQUE (provider_id, country_id, kind)"
                ),
                (
                    "CREATE INDEX shipping_rate_country_active_ix ON "
                    "shipping_shippingrate (country_id, is_active)"
                ),
            ],
        ),
    ]
