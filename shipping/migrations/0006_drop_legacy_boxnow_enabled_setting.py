"""Delete the legacy ``BOXNOW_ENABLED`` ``extra_settings`` row.

``ShippingProvider(code="boxnow").is_active`` is now the single
source of truth for whether BoxNow appears at checkout. The
``BOXNOW_ENABLED`` ``Setting`` row predates the provider registry
and was kept around as a secondary gate during the dual-write
transition — see the old comment chain in
``shipping/migrations/0002_seed_providers.py``. With the order
serializer now reading ``ShippingProvider`` directly and the Nuxt
checkout deriving availability from ``/api/v1/shipping/options``,
the row is dead weight in the admin Settings list.

Lives in the ``shipping`` app (not ``extra_settings``) because we
can't easily ship a migration into a third-party package — using
``apps.get_model("extra_settings", "Setting")`` from any app
migration is fine, the dependency just needs to be declared.

Idempotent: ``.filter(...).delete()`` is a no-op when the row
isn't present (fresh DB / already-removed). Reverse is a no-op
too — once the code paths are gone there's nothing to read the
restored Setting, so re-creating it would be a lie.
"""

from __future__ import annotations

from django.db import migrations


def drop_boxnow_enabled(apps, _schema_editor):
    Setting = apps.get_model("extra_settings", "Setting")
    Setting.objects.filter(name="BOXNOW_ENABLED").delete()


def noop_reverse(apps, _schema_editor):
    return


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0005_seed_acs_print_type"),
        # Touch extra_settings so this migration applies after the
        # Setting model is created.
        ("extra_settings", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(drop_boxnow_enabled, noop_reverse),
    ]
