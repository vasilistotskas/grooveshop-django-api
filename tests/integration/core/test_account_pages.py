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
from unittest import mock

import pytest
from allauth.account.models import EmailAddress
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.utils import translation

from tenant.credentials import _DEFAULT_EMAIL_THEME
from user.factories.account import UserAccountFactory

# The store's primary colour comes from its published theme
# (tenant.credentials._branded_primary); a stand-in store has none, so
# each test says which it has.
UNBRANDED = mock.patch("tenant.credentials._branded_primary", return_value=None)


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
    request.user = SimpleNamespace(
        is_authenticated=True, email="shopper@example.com"
    )
    return request


@pytest.mark.django_db
class TestSignInPage:
    @UNBRANDED
    def test_wears_the_store(self, _primary, client, bind_tenant):
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
        # Signed out on the sign-in page itself: no "Sign In" link to it.
        assert "account-session" not in html

    @UNBRANDED
    def test_signed_out_pages_offer_sign_in(
        self, _primary, client, bind_tenant
    ):
        bind_tenant(_store())
        html = client.get("/accounts/password/reset/").content.decode()
        assert "account-session" in html
        assert "/accounts/login/" in html

    @mock.patch(
        "tenant.credentials._branded_primary",
        return_value=("#0f766e", "#115e59"),
    )
    def test_wears_the_store_colour(self, _primary, client, bind_tenant):
        bind_tenant(_store())
        html = client.get("/accounts/login/").content.decode()
        assert "--brand: #0f766e; --brand-dark: #115e59" in html

    @UNBRANDED
    def test_shows_the_store_logo(self, _primary, client, bind_tenant):
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
    @UNBRANDED
    def test_store_name_not_the_sites_framework_site(
        self, _primary, bind_tenant
    ):
        bind_tenant(_store())
        with translation.override("en"):
            html = render_to_string(
                "idp/oidc/authorization_form.html",
                {
                    "client": SimpleNamespace(name="Claude"),
                    "form": "",
                    "site": SimpleNamespace(name="Another Store"),
                },
                request=_request(),
            )
        # The sentence itself: the name is in the header and footer anyway.
        assert (
            "Claude wants to access your Account Pages Store account." in html
        )
        assert "Another Store" not in html
        # allauth tags the grant "primary" and the cancel "outline".
        assert "account-btn-primary" in html
        assert "account-btn-outline" in html
        # The shopper sees which account the app gets, and can switch.
        assert "shopper@example.com" in html
        # Signing out comes back here, so the shopper can switch account
        # and carry on with the consent.
        assert "/accounts/logout/?next=/identity/o/authorize" in html


class TestSocialSignupNamesTheStore:
    @UNBRANDED
    def test_store_name_not_the_sites_framework_site(
        self, _primary, bind_tenant
    ):
        bind_tenant(_store())
        provider = SimpleNamespace(name="Google")
        with translation.override("en"):
            html = render_to_string(
                "socialaccount/signup.html",
                {
                    "account": SimpleNamespace(get_provider=lambda: provider),
                    "form": None,
                    "site": SimpleNamespace(name="Another Store"),
                },
                request=_request(),
            )
        # The sentence itself: the name is in the header and footer anyway.
        assert "login to\nAccount Pages Store. As a final step" in html
        assert "Another Store" not in html


@pytest.mark.django_db
class TestDestructiveButtons:
    @UNBRANDED
    def test_remove_email_is_a_danger_button(
        self, _primary, client, bind_tenant
    ):
        bind_tenant(_store())
        user = UserAccountFactory(email="shopper@example.com")
        EmailAddress.objects.create(
            user=user, email=user.email, primary=True, verified=True
        )
        # A change waiting for confirmation: the page offers to remove it.
        EmailAddress.objects.create(
            user=user, email="new@example.com", primary=False, verified=False
        )
        client.force_login(user)
        html = client.get("/accounts/email/").content.decode()
        # allauth tags removing the pending address "danger,minor"; it
        # must not look like the primary action on the page.
        assert "account-btn-danger" in html
