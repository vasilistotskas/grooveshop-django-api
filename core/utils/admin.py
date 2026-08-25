"""Admin helpers that depend on the active tenant schema."""

from __future__ import annotations

from django.http import HttpRequest


def command_search_models(request: HttpRequest) -> bool | list[str]:
    """Scope the unfold ⌘K palette's model search to the active schema.

    Tenant-schema admins search every registered model. The public
    (platform) admin may only search SHARED_APPS models — every other
    admin-registered model's table exists solely inside tenant schemas,
    so including it raises ProgrammingError on each palette keystroke.
    """
    from django.db import connection
    from django_tenants.utils import get_public_schema_name

    if connection.schema_name != get_public_schema_name():
        return True
    return [
        "tenant.Tenant",
        "tenant.TenantDomain",
        "tenant.UserTenantMembership",
        "user.UserAccount",
        # extra_settings.Setting deliberately absent — the model is no
        # longer registered on the platform site (store-scoped knobs;
        # see admin/platform_site.py PLATFORM_APP_LABELS).
        "django_celery_beat.PeriodicTask",
        "country.Country",
        "region.Region",
    ]
