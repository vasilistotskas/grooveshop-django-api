"""Phase 2: enable ACS Smartpoint locker pickup capability.

Toggles ``ShippingProvider(code='acs').supports_pickup_point`` to True
so the carrier registry surfaces the kind through ``available_options``.
The customer-facing on/off switch is the ``ACS_SMARTPOINT_ENABLED``
extra-setting (added to ``settings.EXTRA_SETTINGS_DEFAULTS`` in the same
release) — keep both gates because:

* ``supports_pickup_point`` advertises capability (admin diagnostic).
* ``ACS_SMARTPOINT_ENABLED`` lets ops disable just the locker flow
  without touching home delivery.

Idempotent: re-running the migration is a no-op.
"""

from __future__ import annotations

from django.db import migrations


def enable_pickup_point(apps, schema_editor):
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")
    ShippingProvider.objects.filter(code="acs").update(
        supports_pickup_point=True
    )


def disable_pickup_point(apps, schema_editor):
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")
    ShippingProvider.objects.filter(code="acs").update(
        supports_pickup_point=False
    )


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0002_seed_providers"),
    ]

    operations = [
        migrations.RunPython(
            enable_pickup_point, reverse_code=disable_pickup_point
        ),
    ]
