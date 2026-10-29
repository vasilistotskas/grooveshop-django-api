"""A store's admin wears that store's marks and never another store's.

``admin/branding.py`` follows the rule outbound email follows: the
tenant's own asset, the platform's bundled assets only for the platform
storefront, nothing otherwise (Unfold then draws ``SITE_SYMBOL``).
Before, ``UNFOLD["SITE_LOGO"]`` pointed at the bundled SVGs for every
store, so each store's admin showed tenant #1's wordmark.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.contrib.admin import site as admin_site
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from admin import branding


def _tenant(**fields):
    defaults = {
        "schema_name": "branding_store",
        "name": "branding-store",
        "store_name": "Branding Store",
        "logo_light_url": "",
        "logo_dark_url": "",
        "favicon_url": "",
        "is_platform_storefront": False,
    }
    return SimpleNamespace(**{**defaults, **fields})


def _request():
    return RequestFactory().get("/admin/")


class TestSiteLogo:
    def test_own_logos(self, bind_tenant):
        bind_tenant(
            _tenant(
                logo_light_url="https://cdn.example/light.svg",
                logo_dark_url="https://cdn.example/dark.svg",
            )
        )
        assert branding.site_logo(_request()) == {
            "light": "https://cdn.example/light.svg",
            "dark": "https://cdn.example/dark.svg",
        }

    def test_one_logo_serves_both_themes(self, bind_tenant):
        bind_tenant(_tenant(logo_dark_url="https://cdn.example/dark.svg"))
        assert branding.site_logo(_request()) == {
            "light": "https://cdn.example/dark.svg",
            "dark": "https://cdn.example/dark.svg",
        }

    def test_platform_storefront_gets_the_bundled_marks(self, bind_tenant):
        bind_tenant(_tenant(is_platform_storefront=True))
        logo = branding.site_logo(_request())
        assert logo["light"].endswith("logo-light.svg")
        assert logo["dark"].endswith("logo-dark.svg")

    def test_unbranded_store_gets_no_logo(self, bind_tenant):
        bind_tenant(_tenant())
        assert branding.site_logo(_request()) is None

    def test_no_bound_store_gets_no_logo(self, bind_tenant):
        bind_tenant(None)
        assert branding.site_logo(_request()) is None


class TestSiteIconAndFavicons:
    def test_only_the_platform_storefront_gets_the_bundled_icon(
        self, bind_tenant
    ):
        bind_tenant(_tenant(is_platform_storefront=True))
        assert branding.site_icon(_request())["light"].endswith(
            "icon-light.svg"
        )
        bind_tenant(_tenant())
        assert branding.site_icon(_request()) is None

    def test_own_favicon(self, bind_tenant):
        bind_tenant(_tenant(favicon_url="https://cdn.example/favicon.ico"))
        assert branding.site_favicons(_request()) == [
            {"rel": "icon", "href": "https://cdn.example/favicon.ico"}
        ]

    def test_unbranded_store_gets_no_favicon(self, bind_tenant):
        bind_tenant(_tenant())
        assert branding.site_favicons(_request()) == []


@pytest.mark.django_db
def test_the_store_admin_renders_the_store_logo(bind_tenant):
    bind_tenant(_tenant(logo_light_url="https://cdn.example/light.svg"))
    request = _request()
    request.user = AnonymousUser()
    context = admin_site.each_context(request)
    assert context["site_logo"] == {
        "light": "https://cdn.example/light.svg",
        "dark": "https://cdn.example/light.svg",
    }
    assert context["site_header"] == "Branding Store"


class TestStoreMarkUrl:
    """The square mark a square slot shows: the store's favicon, never its
    logo, whose wordmark the slot crops to a sliver."""

    def test_own_favicon(self):
        tenant = _tenant(
            favicon_url="https://cdn.example/icon-512.png",
            logo_light_url="https://cdn.example/wordmark.png",
        )
        assert branding.store_mark_url(tenant) == (
            "https://cdn.example/icon-512.png"
        )

    def test_a_logo_alone_is_not_a_mark(self):
        tenant = _tenant(logo_light_url="https://cdn.example/wordmark.png")
        assert branding.store_mark_url(tenant) is None

    def test_platform_storefront_gets_the_bundled_icon(self):
        tenant = _tenant(is_platform_storefront=True)
        assert branding.store_mark_url(tenant).endswith("icon-light.svg")

    def test_unbranded_store_has_none(self):
        assert branding.store_mark_url(_tenant()) is None


class TestTenantListShowsTheMark:
    def test_the_store_column_shows_the_square_mark(self):
        from tenant.admin import TenantAdmin
        from tenant.models import Tenant

        domain = SimpleNamespace(domain="shop.example", is_primary=True)
        tenant = _tenant(
            favicon_url="https://cdn.example/icon-512.png",
            logo_light_url="https://cdn.example/wordmark.png",
            domains=SimpleNamespace(all=lambda: [domain]),
        )

        row = TenantAdmin(Tenant, admin_site).display_store(tenant)

        assert row[3]["path"] == "https://cdn.example/icon-512.png"
        assert row[3]["squared"] is True
