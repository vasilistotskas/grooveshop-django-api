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


def _label(entry: str) -> str:
    """The app label an ``INSTALLED_APPS`` entry registers.

    The label is the AppConfig's, not the entry's last dotted part:
    ``allauth.idp.oidc`` registers ``allauth_idp_oidc``. Reading it off
    the dotted path gave ``oidc``, so that app's tables were not known
    as tenant-only, and deleting a platform identity reached them in the
    public schema, where they do not exist.
    """
    for config in apps.get_app_configs():
        dotted = f"{type(config).__module__}.{type(config).__qualname__}"
        if entry in (config.name, dotted):
            return config.label
    return entry.rsplit(".", 1)[-1]


def tenant_only_app_labels() -> list[str]:
    """App labels whose tables live only in tenant schemas.

    ``SHARED_APPS`` membership wins: an app in BOTH lists (``user``,
    ``extra_settings``, ``sessions``) has a public copy and is therefore
    not tenant-only.
    """
    shared = {_label(app) for app in settings.SHARED_APPS}
    return [
        label
        for label in dict.fromkeys(_label(app) for app in settings.TENANT_APPS)
        if label not in shared
    ]


def shared_only_app_labels() -> list[str]:
    """App labels whose tables live only in the public schema.

    The mirror of ``tenant_only_app_labels``, and the answer to the
    opposite question: which model pages must a TENANT host not offer,
    because what they would show is the platform's own rows rather than
    that store's. ``django_site`` holds one row per store domain;
    ``core_cachepurgelog`` every purge across the estate;
    ``django_celery_beat`` the schedule that drives every tenant.

    Derived, not enumerated, and that matters here:
    ``role_scopes.PLATFORM_ONLY_APP_LABELS`` looks like the same set but
    is not — it also lists ``allauth_idp_oidc``, whose tables are
    TENANT-only. Hiding that from a tenant host would remove the OIDC
    clients a store genuinely owns. Membership of ``TENANT_APPS`` is the
    honest test of "does this store have its own copy".

    The comparison is on the app LABELS the entries register
    (``_label``), not on the raw ``INSTALLED_APPS`` strings: an app
    named by its module path in one list and by its AppConfig class in
    the other is still one app. ``unfold.contrib.simple_history``
    registers ``unfold_simple_history``, a different app from
    ``simple_history`` (which models the history tables), so the latter
    stays per-tenant.
    """

    tenant_labels = {_label(app) for app in settings.TENANT_APPS}
    return [
        label
        for label in dict.fromkeys(_label(app) for app in settings.SHARED_APPS)
        if label not in tenant_labels
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
