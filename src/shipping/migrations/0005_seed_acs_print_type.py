"""Seed ``print_type=1`` (thermal) into ACS ``ShippingProvider.metadata``.

Ops uses thermal printers (single voucher per roll); the previous code
path hardcoded ``Print_Type=2`` (laser, 4 vouchers per A4) in the ACS
``Print_Voucher`` client default, which produced an A4 sheet with one
voucher in the top-left and the other three slots blank. Surfacing
the value into metadata lets an admin flip back to laser from Django
admin without a redeploy.

Idempotent: only writes ``print_type`` if it isn't already set on the
row, mirroring the merge strategy from
``0004_seed_provider_metadata``. The ACS provider row is expected to
exist (seeded by ``0002_seed_providers``) — if it doesn't, the
migration is a no-op so test bootstrap doesn't crash.
"""

from __future__ import annotations

from django.db import migrations


def seed_print_type(apps, _schema_editor):
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")
    provider = ShippingProvider.objects.filter(code="acs").first()
    if provider is None:
        return
    metadata = dict(provider.metadata or {})
    if "print_type" in metadata:
        return
    metadata["print_type"] = 1
    provider.metadata = metadata
    provider.save(update_fields=["metadata"])


def unseed_print_type(apps, _schema_editor):
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")
    provider = ShippingProvider.objects.filter(code="acs").first()
    if provider is None:
        return
    metadata = dict(provider.metadata or {})
    metadata.pop("print_type", None)
    provider.metadata = metadata
    provider.save(update_fields=["metadata"])


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0004_seed_provider_metadata"),
    ]

    operations = [
        migrations.RunPython(seed_print_type, reverse_code=unseed_print_type),
    ]
