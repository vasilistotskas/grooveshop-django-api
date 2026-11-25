"""Make a rate's zone required, and keep ``country`` as a column the code
writes for the release before.

Expand step (``docs/migrations.md``). The release before this one may
still serve during the rollout, or come back in a rollback: it prices by
``ShippingRate.country`` alone and inserts rates without a zone. So:

- ``zone`` is required in the state and stays nullable in the database
  for those inserts, until the contract release puts them in their
  country's base zone (``0024``'s function again) and sets ``NOT NULL``.
- ``country`` stays in the state, nullable and not editable: ``save``
  writes the zone's country on a base zone's rate and leaves it empty on
  a region zone's (``ShippingZone.rate_country_id``), so the old release
  finds exactly the rates it can price. The old unique constraint and
  index leave the state only; the database keeps them, and the constraint
  refuses the old release a duplicate of a base zone's rate.

The new uniqueness is ``(provider, zone, kind)``: a carrier has one rate
per kind in a zone, and may have another in another zone of the same
country (the islands). The old release's inserts carry no zone, and
NULLs never collide, so building it cannot fail on them; the table holds
a few rows per store.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("country", "0015_image_flag_servable"),
        ("shipping", "0024_shipping_rate_zone_backfill"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="shippingrate",
                    name="shipping_rate_provider_country_kind_unique",
                ),
                migrations.RemoveIndex(
                    model_name="shippingrate",
                    name="shipping_rate_country_active_ix",
                ),
                migrations.AlterField(
                    model_name="historicalshippingrate",
                    name="country",
                    field=models.ForeignKey(
                        blank=True,
                        db_constraint=False,
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.DO_NOTHING,
                        related_name="+",
                        to="country.country",
                        verbose_name="Country",
                    ),
                ),
                migrations.AlterField(
                    model_name="shippingrate",
                    name="country",
                    field=models.ForeignKey(
                        editable=False,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="country.country",
                        verbose_name="Country",
                    ),
                ),
                migrations.AlterField(
                    model_name="shippingrate",
                    name="zone",
                    field=models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rates",
                        to="shipping.shippingzone",
                        verbose_name="Shipping Zone",
                    ),
                ),
                migrations.AlterModelOptions(
                    name="shippingrate",
                    options={
                        "ordering": ["provider__priority", "zone_id", "kind"],
                        "verbose_name": "Shipping Rate",
                        "verbose_name_plural": "Shipping Rates",
                    },
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="shippingrate",
            constraint=models.UniqueConstraint(
                fields=("provider", "zone", "kind"),
                name="shipping_rate_provider_zone_kind_unique",
            ),
        ),
    ]
