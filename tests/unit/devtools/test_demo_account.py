"""The shared demo logins, and the guards that keep them usable.

A demo store prints an account's password on its own login page. That
makes the account shared and publicly writable, so two properties have
to hold and neither is visible at runtime until it has already failed:

  * the fixtures point at products that EXIST — a renamed slug leaves
    the account with no favourites and no order history, and the seeder
    skips silently rather than failing;
  * the credential mutations that would lock everybody out are refused,
    and refused ONLY on this account — the same code path runs for
    every real customer on every other store.
"""

from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase

from core.api.views import PUBLIC_SETTING_KEYS
from devtools import demo_account
from devtools.demo_catalogue import PRODUCTS


class TestDataset(TestCase):
    def test_every_favourite_is_a_real_product(self):
        slugs = {row.slug for row in PRODUCTS}
        for slug in demo_account.FAVOURITE_SLUGS:
            assert slug in slugs, f"{slug} is not in the demo catalogue"

    def test_every_ordered_product_is_real(self):
        slugs = {row.slug for row in PRODUCTS}
        for order in demo_account.ORDERS:
            assert order.items, "an order with no lines renders as nothing"
            for slug, quantity in order.items:
                assert slug in slugs, f"{slug} is not in the demo catalogue"
                assert quantity >= 1, slug

    def test_orders_are_backdated_and_distinct(self):
        """``days_ago`` doubles as the fixture's identity.

        ``_seed_orders`` matches on ``metadata__demo_seed``, so two
        orders sharing a value would collapse into one on a re-run.
        """
        days = [order.days_ago for order in demo_account.ORDERS]
        assert all(day > 0 for day in days), days
        assert len(set(days)) == len(days), days

    def test_orders_cover_the_statuses_worth_showing(self):
        """A history of four identical rows demonstrates nothing: the
        order list renders tracking, payment state and cancellation
        differently."""
        statuses = {order.status for order in demo_account.ORDERS}
        assert {"DELIVERED", "SHIPPED", "CANCELED"} <= statuses

    def test_points_ledger_reads_as_a_story(self):
        kinds = [kind for kind, _points, _description in demo_account.POINTS]
        assert "BONUS" in kinds and "EARN" in kinds and "REDEEM" in kinds
        total = sum(points for _k, points, _d in demo_account.POINTS)
        assert total > 0, "a negative balance is not a showcase"

    def test_the_two_accounts_are_distinct(self):
        assert demo_account.RETAIL_EMAIL != demo_account.B2B_EMAIL
        assert demo_account.RETAIL_PASSWORD != demo_account.B2B_PASSWORD

    def test_passwords_survive_django_validators(self):
        from django.contrib.auth.password_validation import validate_password

        for password in (
            demo_account.RETAIL_PASSWORD,
            demo_account.B2B_PASSWORD,
        ):
            # A published password that the validators reject could not
            # be set by the seeder in the first place.
            validate_password(password)


class TestShowcaseSettings(TestCase):
    def test_every_key_is_a_declared_store_setting(self):
        declared = {
            entry["name"] for entry in django_settings.EXTRA_SETTINGS_DEFAULTS
        }
        for name in demo_account.showcase_settings():
            assert name in declared, f"{name} is not an EXTRA_SETTINGS default"

    def test_every_key_is_published_to_the_storefront(self):
        """The login card reads them from ``/settings/public``; a key
        left off the allow-list makes the card fail closed and render
        nothing at all."""
        for name in demo_account.showcase_settings():
            assert name in PUBLIC_SETTING_KEYS, name

    def test_the_settings_match_the_accounts_that_are_seeded(self):
        values = demo_account.showcase_settings()
        assert values["DEMO_ACCOUNT_EMAIL"] == demo_account.RETAIL_EMAIL
        assert values["DEMO_ACCOUNT_PASSWORD"] == demo_account.RETAIL_PASSWORD
        assert values["DEMO_ACCOUNT_B2B_EMAIL"] == demo_account.B2B_EMAIL
        assert values["DEMO_ACCOUNT_B2B_PASSWORD"] == demo_account.B2B_PASSWORD


@pytest.mark.django_db
class TestDemoAccountLookup(TestCase):
    """``demo_account_emails`` is the seam every guard reads."""

    def _arm(self, **overrides):
        from extra_settings.models import Setting

        values = {
            "DEMO_ACCOUNT_ENABLED": True,
            "DEMO_ACCOUNT_EMAIL": demo_account.RETAIL_EMAIL,
            "DEMO_ACCOUNT_B2B_EMAIL": demo_account.B2B_EMAIL,
            **overrides,
        }
        for name, value in values.items():
            Setting.objects.update_or_create(
                name=name,
                defaults={
                    "value_type": (
                        Setting.TYPE_BOOL
                        if isinstance(value, bool)
                        else Setting.TYPE_STRING
                    ),
                    "value": value,
                },
            )

    def test_empty_when_the_store_has_not_armed_it(self):
        from core.demo_account import demo_account_emails

        self._arm(DEMO_ACCOUNT_ENABLED=False)
        assert demo_account_emails() == frozenset()

    def test_both_accounts_when_armed(self):
        from core.demo_account import demo_account_emails

        self._arm()
        assert demo_account_emails() == frozenset(
            {demo_account.RETAIL_EMAIL, demo_account.B2B_EMAIL}
        )

    def test_matching_ignores_case_and_padding(self):
        from core.demo_account import is_demo_account

        self._arm()
        user = type(
            "U", (), {"email": f"  {demo_account.RETAIL_EMAIL.upper()} "}
        )
        assert is_demo_account(user())

    def test_an_ordinary_customer_is_not_the_demo_account(self):
        from core.demo_account import is_demo_account

        self._arm()
        user = type("U", (), {"email": "someone@example.com"})
        assert not is_demo_account(user())


@pytest.mark.django_db
class TestGuardMiddleware(TestCase):
    """The guard must bite on the demo account and nowhere else."""

    PATHS = (
        "/_allauth/app/v1/account/password/change",
        "/_allauth/app/v1/account/email",
        "/_allauth/app/v1/account/authenticators/totp",
        "/_allauth/app/v1/account/authenticators/webauthn",
    )

    def _middleware(self, demo_emails):
        from core.middleware import demo_account as module

        original = module.demo_account_emails
        module.demo_account_emails = lambda: frozenset(demo_emails)
        self.addCleanup(setattr, module, "demo_account_emails", original)
        return module.DemoAccountGuardMiddleware(lambda request: _Passed())

    def _request(self, path, email, method="POST"):
        request = getattr(RequestFactory(), method.lower())(path)
        request.user = _User(email)
        return request

    def test_blocks_every_credential_path_for_the_demo_account(self):
        middleware = self._middleware({demo_account.RETAIL_EMAIL})
        for path in self.PATHS:
            response = middleware(
                self._request(path, demo_account.RETAIL_EMAIL)
            )
            assert response.status_code == 403, path

    def test_blocks_the_wholesale_account_too(self):
        middleware = self._middleware(
            {demo_account.RETAIL_EMAIL, demo_account.B2B_EMAIL}
        )
        response = middleware(
            self._request(self.PATHS[0], demo_account.B2B_EMAIL)
        )
        assert response.status_code == 403

    def test_lets_an_ordinary_customer_through(self):
        middleware = self._middleware({demo_account.RETAIL_EMAIL})
        for path in self.PATHS:
            response = middleware(self._request(path, "real@example.com"))
            assert response.status_code == 200, path

    def test_lets_reads_through_even_for_the_demo_account(self):
        """Hiding the account UI would hide a platform feature the demo
        exists to show; only the mutation is refused."""
        middleware = self._middleware({demo_account.RETAIL_EMAIL})
        for path in self.PATHS:
            response = middleware(
                self._request(path, demo_account.RETAIL_EMAIL, method="get")
            )
            assert response.status_code == 200, path

    def test_lets_everything_else_through_for_the_demo_account(self):
        middleware = self._middleware({demo_account.RETAIL_EMAIL})
        for path in (
            "/_allauth/app/v1/account/providers",
            "/_allauth/app/v1/auth/session",
            "/api/v1/order",
        ):
            response = middleware(
                self._request(path, demo_account.RETAIL_EMAIL)
            )
            assert response.status_code == 200, path

    def test_is_a_no_op_on_a_store_with_no_demo_account(self):
        """Every ordinary store: the settings are empty, so the guard
        returns before it looks at the user at all."""
        middleware = self._middleware(set())
        for path in self.PATHS:
            response = middleware(
                self._request(path, demo_account.RETAIL_EMAIL)
            )
            assert response.status_code == 200, path

    def test_ignores_an_anonymous_request(self):
        from django.contrib.auth.models import AnonymousUser

        middleware = self._middleware({demo_account.RETAIL_EMAIL})
        request = RequestFactory().post(self.PATHS[0])
        request.user = AnonymousUser()
        assert middleware(request).status_code == 200


@pytest.mark.django_db
class TestAdapterGuards(TestCase):
    """Password reset is unauthenticated, so the middleware cannot see
    it — the adapter is what covers that path."""

    def _adapter(self, demo_emails):
        from core import demo_account as core_demo
        from tenant.allauth_adapter import TenantAccountAdapter

        original = core_demo.demo_account_emails
        core_demo.demo_account_emails = lambda: frozenset(demo_emails)
        self.addCleanup(setattr, core_demo, "demo_account_emails", original)
        return TenantAccountAdapter()

    def test_refuses_a_password_change_on_the_demo_account(self):
        adapter = self._adapter({demo_account.RETAIL_EMAIL})
        with pytest.raises(ValidationError):
            adapter.set_password(_User(demo_account.RETAIL_EMAIL), "whatever")

    def test_allows_a_password_change_for_everybody_else(self):
        from django.contrib.auth import get_user_model

        adapter = self._adapter({demo_account.RETAIL_EMAIL})
        user = get_user_model().objects.create_user(
            email="real@example.com", password="OriginalPass-1"
        )
        adapter.set_password(user, "ReplacementPass-2")
        user.refresh_from_db()
        assert user.check_password("ReplacementPass-2")

    def test_refuses_to_delete_the_published_address(self):
        """Login is by email, so deleting the address on the card is the
        one email operation that costs everybody their way in."""
        adapter = self._adapter({demo_account.RETAIL_EMAIL})
        address = type(
            "E", (), {"email": demo_account.RETAIL_EMAIL, "primary": True}
        )
        assert adapter.can_delete_email(address()) is False


class _Passed:
    status_code = 200


class _User:
    is_authenticated = True

    def __init__(self, email: str):
        self.email = email
