"""Drop ``shipping_shippingrate.country_id``: the contract of ``0027``.

The release that shipped ``0027`` neither reads nor writes the column, and
it is what runs when this one migrates. The history table keeps its column:
a history row whose country never had a base zone has only that to say
where it applied.

The reverse adds the column back as ``0027`` left it: nullable, with its
foreign key and the two indexes Django gives a foreign key on a text column
(the column's own and its ``varchar_pattern_ops`` one), and empty.
``0027``'s reverse fills it and restores its unique and index.
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
                    "CREATE INDEX shipping_shippingrate_country_id_ix "
                    "ON shipping_shippingrate (country_id)"
                ),
                (
                    "CREATE INDEX shipping_shippingrate_country_id_like "
                    "ON shipping_shippingrate (country_id varchar_pattern_ops)"
                ),
            ],
        ),
    ]
