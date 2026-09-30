"""Every first-party inline renders through Unfold.

``TenantDomainInline`` was Django's own ``admin.TabularInline``: a
Django-styled table inside an Unfold form, with no tab and no
pagination support. Third-party inlines (celery-beat's, allauth's) are
not ours to restyle.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from django.conf import settings
from django.contrib import admin as django_admin
from unfold.admin import BaseInlineMixin

from admin.platform_site import platform_admin_site

BASE_DIR = Path(settings.BASE_DIR).resolve()


def _first_party(cls) -> bool:
    path = Path(inspect.getfile(cls)).resolve()
    return path.is_relative_to(BASE_DIR) and ".venv" not in path.parts


def test_every_inline_is_an_unfold_inline():
    plain = sorted(
        {
            f"{inline.__module__}.{inline.__name__}"
            for site in (django_admin.site, platform_admin_site)
            for model_admin in site._registry.values()
            for inline in getattr(model_admin, "inlines", ())
            if _first_party(inline) and not issubclass(inline, BaseInlineMixin)
        }
    )
    assert plain == []
