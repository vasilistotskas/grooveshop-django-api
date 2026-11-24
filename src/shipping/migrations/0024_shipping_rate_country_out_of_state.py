"""Take ``ShippingRate.country`` out of the state: a rate's zone says where.

Expand step, state only (``docs/migrations.md``): the column, its unique
constraint and its index stay in the database while the release before
this one may still serve and write them (``0023`` made the column
nullable, so the new code's inserts leave it out). ``zone`` is required
in the state; the database keeps it nullable for those old inserts until
the contract release copies them into zones and sets ``NOT NULL``.

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
        ("shipping", "0023_shipping_rate_zone"),
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
                migrations.RemoveField(
                    model_name="historicalshippingrate",
                    name="country",
                ),
                migrations.RemoveField(
                    model_name="shippingrate",
                    name="country",
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
