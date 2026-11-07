"""The pages allauth renders on a store's API host wear that store.

An AI agent sends a shopper to ``/accounts/login/`` and then to the
OAuth consent (``/identity/o/authorize``) on the store's API host. Both
rendered allauth's bare default layout: no stylesheet and a "Menu:"
list. The consent page also named ``django.contrib.sites``'
``SITE_ID`` site, which is one store for every store, so a demo-store
shopper read that Claude "wants to access your Webside account".
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from tenant.credentials import _DEFAULT_EMAIL_THEME


def _store(**fields):
    defaults = {
        "schema_name": "account_pages_store",
        "name": "account-pages-store",
        "store_name": "Account Pages Store",
        "logo_light_url": "",
        "logo_dark_url": "",
        "favicon_url": "",
        "is_platform_storefront": False,
    }
    return SimpleNamespace(**{**defaults, **fields})


def _request():
    request = RequestFactory().get("/identity/o/authorize")
    request.user = SimpleNamespace(is_authenticated=True)
    return request


@pytest.mark.django_db
class TestSignInPage:
    def test_wears_the_store(self, client, bind_tenant):
        bind_tenant(_store())
        response = client.get("/accounts/login/")
        assert response.status_code == 200
        html = response.content.decode()
        assert "css/styles.css" in html
        assert "Account Pages Store" in html
        assert f"--brand: {_DEFAULT_EMAIL_THEME['primary']}" in html
        assert 'class="account-card"' in html
        # allauth's default layout listed its links under "Menu:".
        assert "Menu:" not in html
        assert "Μενού:" not in html

    def test_shows_the_store_logo(self, client, bind_tenant):
        bind_tenant(
            _store(
                logo_light_url="https://cdn.example/light.svg",
                logo_dark_url="https://cdn.example/dark.svg",
            )
        )
        html = client.get("/accounts/login/").content.decode()
        assert 'src="https://cdn.example/light.svg"' in html
        assert 'srcset="https://cdn.example/dark.svg"' in html


class TestConsentNamesTheStore:
    def test_store_name_not_the_sites_framework_site(self, bind_tenant):
        bind_tenant(_store())
        html = render_to_string(
            "idp/oidc/authorization_form.html",
            {
                "client": SimpleNamespace(name="Claude"),
                "form": "",
                "site": SimpleNamespace(name="Another Store"),
            },
            request=_request(),
        )
        assert "Account Pages Store" in html
        assert "Another Store" not in html
        # allauth tags the grant "primary" and the cancel "outline".
        assert "account-btn-primary" in html
        assert "account-btn-outline" in html


class TestSocialSignupNamesTheStore:
    def test_store_name_not_the_sites_framework_site(self, bind_tenant):
        bind_tenant(_store())
        provider = SimpleNamespace(name="Google")
        html = render_to_string(
            "socialaccount/signup.html",
            {
                "account": SimpleNamespace(get_provider=lambda: provider),
                "form": None,
                "site": SimpleNamespace(name="Another Store"),
            },
            request=_request(),
        )
        assert "Account Pages Store" in html
        assert "Another Store" not in html
