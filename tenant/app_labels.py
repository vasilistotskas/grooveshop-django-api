"""Which app labels are tenant-only.

An app listed in ``TENANT_APPS`` but not in ``SHARED_APPS`` owns tables
that exist ONLY inside tenant schemas. Consumers: the admin (which
must not offer their model pages while serving the public schema) and
``tenant.role_scopes`` (which derives the store-scope permission set
from it).
"""

from __future__ import annotations

from django.apps import apps
from django.conf import settings


def tenant_only_app_labels() -> list[str]:
    """App labels whose tables live only in tenant schemas.

    ``SHARED_APPS`` membership wins: an app in BOTH lists (``user``,
    ``extra_settings``, ``sessions``) has a public copy and is therefore
    not tenant-only.
    """
    shared = set(settings.SHARED_APPS)
    return [
        app.split(".")[-1] if "." in app else app
        for app in settings.TENANT_APPS
        if app not in shared
    ]


def tenant_only_table_names() -> set[str]:
    """Database tables owned by tenant-only apps.

    These must never exist in the public schema: ``TenantSyncRouter``
    refuses every tenant-app migration there, so a table that does
    exist can only be pre-cutover residue from when this database was
    a single schema — frozen at its cutover shape, unmigratable, and
    unreachable by the ORM in a public-schema context.

    ``include_auto_created=True`` is load-bearing. Implicit M2M through
    tables (``blog_blogpost_tags``, ``socialaccount_socialapp_sites``,
    the djstripe ``*taxrate`` tables) are not registered models, so
    omitting them leaves 11 tables OUTSIDE the set holding foreign keys
    INTO it — and a ``DROP TABLE`` of the set without CASCADE then
    fails. Measured against production: 138 tables without the flag,
    145 with it, and inbound-FK-from-outside drops from 11 to 0.
    """
    labels = set(tenant_only_app_labels())
    return {
        model._meta.db_table
        for model in apps.get_models(include_auto_created=True)
        if model._meta.managed and model._meta.app_label in labels
    }
