"""Which app labels are tenant-only.

An app listed in ``TENANT_APPS`` but not in ``SHARED_APPS`` owns tables
that exist ONLY inside tenant schemas. Two places need that set and had
been deriving it separately: ``prune_public_legacy_data`` (which
truncates those tables out of ``public``) and the admin (which must not
offer their model pages while serving the public schema).
"""

from __future__ import annotations

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
