"""Seed the ShippingProvider rows for BoxNow + ACS.

Idempotent: re-running update_or_create with the same code does nothing.
Provider apps register their adapter classes at AppConfig.ready(), but
the DB-side row is the on/off switch + capability declaration.

Both providers ship with ``is_active=False`` so a fresh deploy never
exposes the option to checkout until an admin flips the row.
``ShippingProvider.is_active`` is the single source of truth — the
legacy ``BOXNOW_ENABLED`` extra-setting Setting was retired in the
dual-write cleanup pass.
"""

from __future__ import annotations

from django.db import migrations


def seed_providers(apps, schema_editor):
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")
    db_alias = schema_editor.connection.alias

    ShippingProvider.objects.using(db_alias).update_or_create(
        code="boxnow",
        defaults={
            "name": "BOX NOW",
            "is_active": False,
            "supports_home_delivery": False,
            "supports_pickup_point": True,
            "live_mode": False,
            "priority": 20,
            "metadata": {
                "supported_countries": ["GR"],
                "locker_picker_kind": "boxnow_widget",
                "tagline_key": "shipping.method.boxnow.tagline",
                "tagline_color": "info",
                "logo": "/img/shipping/boxnow.png",
            },
        },
    )

    ShippingProvider.objects.using(db_alias).update_or_create(
        code="acs",
        defaults={
            "name": "ACS Courier",
            "is_active": False,
            "supports_home_delivery": True,
            "supports_pickup_point": False,
            "live_mode": False,
            "priority": 10,
            "metadata": {
                "supported_countries": ["GR"],
                "locker_picker_kind": "acs_db_picker",
                "logo": "/img/shipping/acs.png",
            },
        },
    )


def unseed_providers(apps, schema_editor):
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")
    ShippingProvider.objects.using(schema_editor.connection.alias).filter(
        code__in=["boxnow", "acs"]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0001_initial_provider_registry"),
    ]

    operations = [
        migrations.RunPython(seed_providers, reverse_code=unseed_providers),
    ]
