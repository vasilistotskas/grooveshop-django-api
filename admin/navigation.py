"""The store admin's site menu (Unfold ``SITE_DROPDOWN``), per request.

Unfold renders dropdown items without a permission check, so the static
list this replaces showed every store's staff the cache purge, Rosetta
(which refuses outside the public schema, so the link was a dead end on
every store host) and the platform's own ops consoles. Each tool is
listed here only for someone who can open it.
"""

from __future__ import annotations

from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from core.rosetta_access import tenant_scoped_rosetta_access
from tenant.membership import is_platform_superuser

_NEW_TAB = {"target": "_blank", "rel": "noopener"}


def store_site_dropdown(request) -> list[dict]:
    user = request.user
    items = []
    if tenant_scoped_rosetta_access(user):
        items.append(
            {
                "icon": "translate",
                "title": _("Rosetta"),
                "link": reverse("rosetta-file-list-redirect"),
            }
        )
    if user.has_perm("core.purge_cache"):
        items.append(
            {
                "icon": "cached",
                "title": _("Cache"),
                "link": reverse("admin:clear-cache"),
            }
        )
    # The template previews render real orders.
    if user.has_perm("order.view_order"):
        items.append(
            {
                "icon": "email",
                "title": _("Email Templates"),
                "link": reverse("email_templates:management"),
            }
        )
    items.append(
        {
            "icon": "schema",
            "title": _("API Swagger"),
            "link": reverse("swagger-ui"),
            "attrs": _NEW_TAB,
        }
    )
    items.extend(settings.ADMIN_DOCS_LINKS)
    if is_platform_superuser(user):
        items.extend(settings.ADMIN_OPS_LINKS)
    return items
