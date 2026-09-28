"""Drop the shipping-price settings and country lists ``ShippingRate`` replaced.

Release N (``0010``-``0013``, grooveshop-django-api#80) copied every
store's six price settings and each provider's
``metadata["supported_countries"]`` into ``ShippingRate`` rows
(``0011_convert_legacy_pricing_to_rates``) and stopped reading them,
but left the data in place: the release still serving during the
PreSync rollout priced from those rows, and deleting them under it
would have fallen back to the literal defaults in ``Setting.get(...,
default=...)``. That release now runs in production, so nothing reads
them any more and they only clutter the admin Settings list and the
provider metadata.

Runs in every tenant schema, and for a new store after ``0002`` seeded
``supported_countries`` and ``0011`` converted it.

Idempotent: deleting absent rows and popping an absent key are no-ops.
Reverse is a no-op too — no code reads either, so restoring them would
recreate settings that do nothing.
"""

from __future__ import annotations

from django.db import migrations

LEGACY_SETTINGS = (
    "CHECKOUT_SHIPPING_PRICE",
    "FREE_SHIPPING_THRESHOLD",
    "BOXNOW_SHIPPING_PRICE",
    "BOXNOW_FREE_SHIPPING_THRESHOLD",
    "ACS_SHIPPING_PRICE",
    "ACS_FREE_SHIPPING_THRESHOLD",
)


def drop_legacy_shipping_settings(apps, schema_editor):
    db_alias = schema_editor.connection.alias
    Setting = apps.get_model("extra_settings", "Setting")
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")

    Setting.objects.using(db_alias).filter(name__in=LEGACY_SETTINGS).delete()

    for provider in ShippingProvider.objects.using(db_alias).filter(
        metadata__has_key="supported_countries"
    ):
        provider.metadata.pop("supported_countries")
        provider.save(update_fields=["metadata"])


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0013_alter_shippingrate_kind_verbose_name"),
        ("extra_settings", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(
            drop_legacy_shipping_settings, migrations.RunPython.noop
        ),
    ]
