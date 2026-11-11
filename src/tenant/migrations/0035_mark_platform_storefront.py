"""Carry the platform-storefront designation over from env to the row.

Until now the storefront recognised the platform's own store by
comparing each tenant's primary domain with ``NUXT_PUBLIC_BASE_URL``,
and Django's email branding did the same against ``NUXT_BASE_URL``.
That designation is now the ``Tenant.is_platform_storefront`` row flag,
so this migration derives it ONCE from the value every environment
already carries — no tenant is named here — and only when no row holds
the flag yet.
"""

from urllib.parse import urlparse

from django.conf import settings
from django.db import migrations


def mark_platform_storefront(apps, schema_editor):
    Tenant = apps.get_model("tenant", "Tenant")
    TenantDomain = apps.get_model("tenant", "TenantDomain")

    if Tenant.objects.filter(is_platform_storefront=True).exists():
        return

    base_url = getattr(settings, "NUXT_BASE_URL", "") or ""
    host = urlparse(base_url).hostname or ""
    if not host:
        return

    row = (
        TenantDomain.objects.filter(domain=host, is_primary=True)
        .exclude(tenant__schema_name="public")
        .select_related("tenant")
        .first()
    )
    if row is None:
        return

    Tenant.objects.filter(pk=row.tenant_id).update(is_platform_storefront=True)


class Migration(migrations.Migration):
    dependencies = [
        ("tenant", "0034_tenant_platform_storefront_and_seo"),
    ]

    operations = [
        migrations.RunPython(
            mark_platform_storefront, migrations.RunPython.noop
        ),
    ]
