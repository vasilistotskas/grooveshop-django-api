"""Put every rate the release before 0022 wrote in a zone: ``0024`` again.

That release prices by ``country`` alone, and during the expand release's
rollout it inserted rates without a zone and could re-point one to another
country. ``zone_every_rate`` says what becomes of each (a rate whose zone
is already priced is deleted and its id logged). It runs before ``0027``
makes ``zone`` NOT NULL, and in a migration of its own
(``docs/migrations.md``). Run again it finds nothing.

``0025`` made ``zone`` required in the state while the column stayed
nullable. In that state the ORM joins a rate to its zone with an INNER
JOIN, and the rates without one are never found. So the state says what
the database does, nullable, while the rates are read.
"""

from importlib import import_module

import django.db.models.deletion
from django.db import migrations, models

zone_every_rate = import_module(
    "shipping.migrations.0024_shipping_rate_zone_backfill"
).zone_every_rate


def _zone_in_state(*, null):
    return migrations.SeparateDatabaseAndState(
        database_operations=[],
        state_operations=[
            migrations.AlterField(
                model_name="shippingrate",
                name="zone",
                field=models.ForeignKey(
                    null=null,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="rates",
                    to="shipping.shippingzone",
                    verbose_name="Shipping Zone",
                ),
            ),
        ],
    )


class Migration(migrations.Migration):

    dependencies = [
        ("country", "0015_image_flag_servable"),
        ("shipping", "0025_shipping_rate_zone_required"),
    ]

    operations = [
        _zone_in_state(null=True),
        migrations.RunPython(
            zone_every_rate, migrations.RunPython.noop, elidable=False
        ),
        _zone_in_state(null=False),
    ]
