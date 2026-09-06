"""The control-plane host must not serve the store admin, under any locale.

`tenant/urls_public.py` mounts `platform_admin_site` at the UNPREFIXED
`admin/` and lists it first so it shadows the shared one. But the store
admin sat in `_shared_i18n_patterns`, which `public_shared_urlpatterns`
includes — so the shadowing covered only the unprefixed path:

    /admin/login/          -> PlatformAdminSite   (superuser only)
    /en/admin/login/       -> MyAdminSite         (any is_staff identity)
    /en/admin/clear-cache/ -> MyAdminSite.clear_cache_view

A store owner could open the cache page there and purge globally: on the
public schema `_current_tenant_host()` returns None, so the Nuxt purge
goes out with no host and flushes every store's SSR cache.

Parametrised over every configured language because the defect was only
reachable under a non-default one — `prefix_default_language=False`
means the prefixed form exists for exactly those.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.urls import Resolver404, resolve
from django.utils import translation

from admin.admin import MyAdminSite

_LANGUAGES = [code for code, _name in settings.LANGUAGES]
_ADMIN_PATHS = [
    "/admin/login/",
    "/admin/clear-cache/",
    "/admin/",
]


def _resolved_site(path, urlconf):
    try:
        match = resolve(path, urlconf=urlconf)
    except Resolver404:
        return None
    owner = getattr(match.func, "__self__", None)
    return type(owner) if owner is not None else None


@pytest.mark.parametrize("language", _LANGUAGES)
@pytest.mark.parametrize("path", _ADMIN_PATHS)
def test_the_store_admin_is_not_reachable_on_the_platform_host(language, path):
    for candidate in (path, f"/{language}{path}"):
        with translation.override(language):
            site = _resolved_site(candidate, "tenant.urls_public")

        assert site is not MyAdminSite, (
            f"{candidate} reaches the STORE admin on the control-plane "
            f"host under language {language!r}"
        )


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_platform_admin_is_still_reachable(language):
    """The fix must not take the control plane down with it."""
    with translation.override(language):
        site = _resolved_site("/admin/login/", "tenant.urls_public")

    assert site is not None, "the platform admin login stopped resolving"
    assert site is not MyAdminSite


@pytest.mark.parametrize("language", _LANGUAGES)
def test_the_store_admin_is_still_reachable_on_a_tenant_host(language):
    """And must not take the merchant admin down either.

    `prefix_default_language=False`, so exactly one of the two forms
    resolves for a given active language — which one is not the point.
    """
    with translation.override(language):
        sites = {
            _resolved_site(path, "core.urls")
            for path in ("/admin/login/", f"/{language}/admin/login/")
        }

    assert MyAdminSite in sites, (
        f"the store admin resolves under neither form for {language!r}"
    )
