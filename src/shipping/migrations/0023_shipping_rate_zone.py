"""Give every shipping rate a zone column: the schema half.

Expand step of moving ``ShippingRate.country`` onto ``ShippingRate.zone``
(``docs/migrations.md``):

1. ``country`` becomes nullable: the new code stops writing it. First,
   so the reverse makes it NOT NULL again only after it has been filled.
2. ``zone`` is added nullable: the release still serving during the
   rollout inserts rates without it.

``0024`` puts each rate in its country's zone. It is a migration of its
own: the index and foreign key of ``zone`` are built when this migration
ends, and a store provisioned in one transaction has rates written in it
(``0011``), whose rows would still have queued checks against them if the
backfill ran first ("cannot CREATE INDEX ... pending trigger events").
``0025`` takes ``country`` out of the state; the contract release drops it
after copying any rate the old release wrote meanwhile.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("country", "0015_image_flag_servable"),
        ("shipping", "0022_shipping_zone"),
    ]

    operations = [
        migrations.AlterField(
            model_name="shippingrate",
            name="country",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="country.country",
                verbose_name="Country",
            ),
        ),
        migrations.AddField(
            model_name="historicalshippingrate",
            name="zone",
            field=models.ForeignKey(
                blank=True,
                db_constraint=False,
                null=True,
                on_delete=django.db.models.deletion.DO_NOTHING,
                related_name="+",
                to="shipping.shippingzone",
                verbose_name="Shipping Zone",
            ),
        ),
        migrations.AddField(
            model_name="shippingrate",
            name="zone",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rates",
                to="shipping.shippingzone",
                verbose_name="Shipping Zone",
            ),
        ),
    ]
