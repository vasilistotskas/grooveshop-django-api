"""Seed the built-in flat-rate carrier row.

Unlike ACS and BoxNow, this adapter has no credentials to gate on, so
the row is the only switch it has. It is still seeded INACTIVE: this
migration runs against every existing tenant schema, and turning on a
new checkout option for a live store is a commercial decision its
merchant makes, not one a deploy makes for them.

New tenants get it switched ON at provisioning time
(``tenant.provisioning``), which is what removes the onboarding cliff —
a freshly created store could otherwise render an empty delivery step
until it signed a courier contract.
"""

from __future__ import annotations

from django.db import migrations

CODE = "flat_rate"


def seed_flat_rate(apps, schema_editor):
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")
    db_alias = schema_editor.connection.alias

    ShippingProvider.objects.using(db_alias).update_or_create(
        code=CODE,
        defaults={
            "name": "Standard delivery",
            "is_active": False,
            "supports_home_delivery": True,
            "supports_pickup_point": False,
            # Nothing is sandboxed — there is no remote system to be in
            # test mode against. True so the admin does not warn that
            # vouchers will not really ship; none are minted.
            "live_mode": True,
            # After the real couriers, so a store with a contract leads
            # with it and keeps this as the fallback option.
            "priority": 90,
            # No tagline: the storefront labels a home-delivery option
            # from `shipping.method.home_delivery.label`, and a key that
            # does not exist in the locale files renders as the raw key.
            # `name` here is the admin-facing label only.
            "metadata": {},
        },
    )


def unseed_flat_rate(apps, schema_editor):
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")
    ShippingProvider.objects.using(
        schema_editor.connection.alias
    ).filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0008_shippingprovider_logo_pickup_point_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_flat_rate, unseed_flat_rate),
    ]
