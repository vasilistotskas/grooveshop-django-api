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
from django.test import TestCase

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
class TestAdapterGuards(TestCase):
    """The adapters are the only seam that works for the app flow.

    `/_allauth/app/v1/**` resolves its session token inside a VIEW
    DECORATOR, so `request.user` is still anonymous while middleware
    runs — a middleware guard never fired against staging and the
    request fell through to `set_password`, which 500'd. These hooks run
    where allauth has already resolved the user.
    """

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

    def test_refuses_a_new_password_during_validation(self):
        """``clean_password`` is the hook that produces a USABLE refusal:
        it runs inside form validation, so allauth renders a 400 with a
        message instead of the 500 a post-validation raise gives."""
        adapter = self._adapter({demo_account.RETAIL_EMAIL})
        with pytest.raises(ValidationError):
            adapter.clean_password(
                "Whatever-2026", user=_User(demo_account.RETAIL_EMAIL)
            )

    def test_allows_a_new_password_for_everybody_else(self):
        from django.contrib.auth import get_user_model

        adapter = self._adapter({demo_account.RETAIL_EMAIL})
        user = get_user_model().objects.create_user(
            email="real@example.com", password="OriginalPass-1"
        )
        assert adapter.clean_password("ReplacementPass-2", user=user)

    def test_signup_is_unaffected(self):
        """Signup calls ``clean_password`` with no user at all."""
        adapter = self._adapter({demo_account.RETAIL_EMAIL})
        assert adapter.clean_password("BrandNewPass-3", user=None)


@pytest.mark.django_db
class TestMfaCannotLockOutTheDemoAccount(TestCase):
    """Enrolment is left alone; the LOCKOUT is what is prevented.

    allauth 65.19 has no pre-enrolment adapter hook, and blocking it
    would mean reaching into internals. But the login stage asks
    ``is_mfa_enabled`` before demanding a code, so answering False means
    a factor a visitor enrolled cannot stop the next person signing in.
    """

    def _adapter(self, demo_emails):
        from core import demo_account as core_demo
        from core.adapter import MFAAdapter

        original = core_demo.demo_account_emails
        core_demo.demo_account_emails = lambda: frozenset(demo_emails)
        self.addCleanup(setattr, core_demo, "demo_account_emails", original)
        return MFAAdapter()

    def test_never_demanded_for_a_demo_login(self):
        adapter = self._adapter({demo_account.RETAIL_EMAIL})
        assert adapter.is_mfa_enabled(_User(demo_account.RETAIL_EMAIL)) is False

    def test_the_wholesale_login_too(self):
        adapter = self._adapter(
            {demo_account.RETAIL_EMAIL, demo_account.B2B_EMAIL}
        )
        assert adapter.is_mfa_enabled(_User(demo_account.B2B_EMAIL)) is False

    def test_a_real_customer_keeps_their_second_factor(self):
        """The whole point: this must not weaken anybody else's account."""
        from django.contrib.auth import get_user_model

        adapter = self._adapter({demo_account.RETAIL_EMAIL})
        user = get_user_model().objects.create_user(
            email="real@example.com", password="OriginalPass-1"
        )
        # No authenticators, so False — but via the real implementation,
        # not the demo short-circuit. Enrolling one would flip it.
        assert adapter.is_mfa_enabled(user) is False
        from allauth.mfa.models import Authenticator

        Authenticator.objects.create(
            user=user, type=Authenticator.Type.TOTP, data={"secret": "x"}
        )
        assert adapter.is_mfa_enabled(user) is True


@pytest.mark.django_db
class TestLocales(TestCase):
    """The demo store has to SERVE the English it was given.

    Everything the seeder writes is bilingual, and none of it is
    reachable while `available_locales` is empty: empty means
    single-language, so the storefront 404s `/en` and hides it from the
    switcher, the hreflang set and the sitemap. Staging answered 404 on
    a store whose every string already had an English translation.
    """

    def test_skips_a_tenant_that_is_not_a_demo(self):
        from devtools import demo_store

        # No demo tenant in this schema, so nothing to do — and nothing
        # that could start publishing a second locale on a real store.
        assert demo_store.seed_locales() == {"skipped_not_a_demo_tenant": 1}

    def test_the_list_it_would_write_is_valid(self):
        """Whatever it writes must pass the field validator, which
        `save()` does not run — hence the `full_clean` in the step."""
        from django.conf import settings as django_conf

        from tenant.validators import validate_available_locales

        allowed = {code for code, _label in django_conf.LANGUAGES}
        locales = list(dict.fromkeys(["el", "en"]))
        assert set(locales) <= allowed, (locales, allowed)
        validate_available_locales(locales)

    def test_the_default_locale_is_never_duplicated(self):
        """A store whose default IS `en` would otherwise be given it
        twice, which the validator rejects."""
        from tenant.validators import validate_available_locales

        validate_available_locales(list(dict.fromkeys(["en", "en"])))


class _Passed:
    status_code = 200


class _User:
    is_authenticated = True

    def __init__(self, email: str):
        self.email = email


@pytest.mark.django_db
class TestMailSuppression(TestCase):
    """Mail addressed to a shared demo login is dropped before delivery.

    A stranger walking the checkout or asking for a password reset
    generates mail the operator never asked for, to an address that may
    not even forward — and a bounce counts against the platform's
    sending domain.
    """

    def _backend(self, demo_emails):
        from core import mail as core_mail

        original = core_mail.demo_account_emails
        core_mail.demo_account_emails = lambda: frozenset(demo_emails)
        self.addCleanup(setattr, core_mail, "demo_account_emails", original)
        with self.settings(
            EMAIL_DELEGATE_BACKEND=(
                "django.core.mail.backends.locmem.EmailBackend"
            )
        ):
            return core_mail.DemoRecipientSuppressingBackend()

    @staticmethod
    def _message(to, cc=None):
        from django.core.mail import EmailMessage

        return EmailMessage(
            subject="s", body="b", to=list(to), cc=list(cc or [])
        )

    def test_drops_a_message_addressed_only_to_the_demo_account(self):
        from django.core import mail as django_mail

        backend = self._backend({demo_account.RETAIL_EMAIL})
        with self.settings(
            EMAIL_DELEGATE_BACKEND=(
                "django.core.mail.backends.locmem.EmailBackend"
            )
        ):
            django_mail.outbox = []
            sent = backend.send_messages(
                [self._message([demo_account.RETAIL_EMAIL])]
            )
        assert sent == 0
        assert django_mail.outbox == []

    def test_keeps_a_message_to_a_real_customer(self):
        from django.core import mail as django_mail

        backend = self._backend({demo_account.RETAIL_EMAIL})
        with self.settings(
            EMAIL_DELEGATE_BACKEND=(
                "django.core.mail.backends.locmem.EmailBackend"
            )
        ):
            django_mail.outbox = []
            sent = backend.send_messages([self._message(["real@example.com"])])
        assert sent == 1
        assert len(django_mail.outbox) == 1

    def test_strips_the_demo_address_from_a_mixed_recipient_list(self):
        from django.core import mail as django_mail

        backend = self._backend({demo_account.RETAIL_EMAIL})
        with self.settings(
            EMAIL_DELEGATE_BACKEND=(
                "django.core.mail.backends.locmem.EmailBackend"
            )
        ):
            django_mail.outbox = []
            backend.send_messages(
                [self._message(["real@example.com", demo_account.RETAIL_EMAIL])]
            )
        assert django_mail.outbox[0].to == ["real@example.com"]

    def test_strips_a_demo_address_hidden_in_cc(self):
        from django.core import mail as django_mail

        backend = self._backend({demo_account.RETAIL_EMAIL})
        with self.settings(
            EMAIL_DELEGATE_BACKEND=(
                "django.core.mail.backends.locmem.EmailBackend"
            )
        ):
            django_mail.outbox = []
            backend.send_messages(
                [
                    self._message(
                        ["real@example.com"],
                        cc=[demo_account.RETAIL_EMAIL],
                    )
                ]
            )
        assert django_mail.outbox[0].cc == []

    def test_matches_a_display_name_form(self):
        from django.core import mail as django_mail

        backend = self._backend({demo_account.RETAIL_EMAIL})
        with self.settings(
            EMAIL_DELEGATE_BACKEND=(
                "django.core.mail.backends.locmem.EmailBackend"
            )
        ):
            django_mail.outbox = []
            sent = backend.send_messages(
                [self._message([f"Demo <{demo_account.RETAIL_EMAIL}>"])]
            )
        assert sent == 0

    def test_is_a_passthrough_on_a_store_with_no_demo_account(self):
        from django.core import mail as django_mail

        backend = self._backend(set())
        with self.settings(
            EMAIL_DELEGATE_BACKEND=(
                "django.core.mail.backends.locmem.EmailBackend"
            )
        ):
            django_mail.outbox = []
            sent = backend.send_messages(
                [self._message([demo_account.RETAIL_EMAIL])]
            )
        assert sent == 1
        assert len(django_mail.outbox) == 1
