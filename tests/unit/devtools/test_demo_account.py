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

import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import pytest
from django.conf import settings as django_settings
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

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
        assert {"COMPLETED", "SHIPPED", "CANCELED"} <= statuses

    def test_every_fixture_is_a_state_a_real_order_can_hold(self):
        """``complete_paid_delivered_orders`` moves DELIVERED + paid to
        COMPLETED within the hour, so a DELIVERED fixture only re-entered
        the state machine; and a paid order always owes its money through
        a settlement that can collect it."""
        from pay_way.enum.settlement import PaySettlement

        settlements = {value for value, _label in PaySettlement.choices}
        for order in demo_account.ORDERS:
            assert order.status != "DELIVERED", order
            assert order.settlement in settlements, order

    def test_points_ledger_reads_as_a_story(self):
        kinds = {row.transaction_type for row in demo_account.POINTS}
        assert {"BONUS", "EARN", "REDEEM"} <= kinds
        total = sum(row.points for row in demo_account.POINTS)
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
        # A fixed username: a generated ``{Adjective}{Noun}`` one could
        # resemble the password, which the similarity validator refuses.
        user = get_user_model().objects.create_user(
            email="real@example.com",
            username="real-shopper",
            password="OriginalPass-1",
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

        from tenant.models import Tenant

        allowed = {code for code, _label in django_conf.LANGUAGES}
        locales = list(dict.fromkeys(["el", "en"]))
        assert set(locales) <= allowed, (locales, allowed)
        Tenant._meta.get_field("available_locales").run_validators(locales)

    def test_the_default_locale_is_never_duplicated(self):
        """A store whose default IS `en` would otherwise be given it
        twice, which the validator rejects."""
        from tenant.models import Tenant

        Tenant._meta.get_field("available_locales").run_validators(
            list(dict.fromkeys(["en", "en"]))
        )


class TestEnglishLegalDocuments(TestCase):
    """The documents that unblock the English locale.

    `Tenant` refuses a locale whose legal documents have no body in it,
    so these are what let the demo store serve `/en` at all.
    """

    def test_covers_every_slug_the_validator_checks(self):
        from devtools import demo_legal
        from page_config.legal_documents import LEGAL_DOCUMENT_SLUGS

        assert set(demo_legal.DOCUMENTS) == set(LEGAL_DOCUMENT_SLUGS)

    def test_section_ids_match_the_greek_document(self):
        """The legal route builds its table of contents from these ids.

        A translation that renames them gives the English page a TOC
        whose every anchor resolves to nothing — which is the bug the
        old compiled-in boilerplate shipped with on tenant #2.
        """
        import re

        from devtools import demo_legal
        from page_config.legal_documents import LEGAL_DOCUMENTS

        def ids(html: str) -> list[str]:
            return re.findall(r'<section id="([a-z0-9-]+)"', html)

        for slug, document in demo_legal.DOCUMENTS.items():
            assert ids(document["body"]) == ids(
                LEGAL_DOCUMENTS[slug]["body"]
            ), slug

    def test_substitutions_are_resolved(self):
        from devtools import demo_legal

        for slug in demo_legal.DOCUMENTS:
            rendered = demo_legal.render(
                slug, site_host="demo.example", store_name="Demo Store"
            )
            assert "{site_host}" not in rendered, slug
            assert "{store_name}" not in rendered, slug
            assert "demo.example" in rendered or "Demo Store" in rendered

    def test_each_document_says_it_is_a_demonstration(self):
        """A published legal page that reads as a real shop's is the one
        way this content could mislead somebody."""
        from devtools import demo_legal

        for slug in ("terms", "privacy"):
            body = demo_legal.DOCUMENTS[slug]["body"].lower()
            assert "demonstration" in body, slug


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

        # `demo_store_mailboxes`, not `demo_account_emails`: the set the
        # backend suppresses is the shared LOGINS plus the addresses the
        # demo store publishes for itself, which have no mailbox behind
        # them either.
        original = core_mail.demo_store_mailboxes
        core_mail.demo_store_mailboxes = lambda: frozenset(demo_emails)
        self.addCleanup(setattr, core_mail, "demo_store_mailboxes", original)
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


class TestDemoStoreMailboxes(TestCase):
    """The store's own published address is a suppressed address too.

    A demo store publishes a mailbox on its OWN domain — the footer and
    the contact page of a public showcase must not carry the operator's
    inbox, which is what they carried until 2026-09-19. Nothing is
    listening behind it, so a contact-form notification sent there is
    the bounce `core.mail` exists to prevent.
    """

    def test_publishes_on_the_stores_own_domain(self):
        """The address follows the tenant, never a hardcoded host.

        The showcase is `demo.grooveshop.space` in production and
        `demo-staging.grooveshop.space` on staging; one literal would
        make one of them publish the other's address.
        """
        from devtools import demo_store

        with mock.patch(
            "page_config.defaults.tenant_document_context",
            return_value=("demo-staging.grooveshop.space", "GrooveShop Demo"),
        ):
            self.assertEqual(
                demo_store._demo_contact_email(),
                "hello@demo-staging.grooveshop.space",
            )

    def test_falls_back_to_the_platform_host_not_a_personal_mailbox(self):
        from devtools import demo_store

        with (
            mock.patch(
                "page_config.defaults.tenant_document_context",
                return_value=("", "GrooveShop Demo"),
            ),
            self.settings(
                APP_MAIN_HOST_NAME="grooveshop.space",
                INFO_EMAIL="someone@personal.example",
            ),
        ):
            resolved = demo_store._demo_contact_email()

        self.assertEqual(resolved, "hello@grooveshop.space")
        self.assertNotIn("personal.example", resolved)

    def test_the_published_address_joins_the_suppressed_set(self):
        from core import demo_account as module

        with (
            mock.patch.object(
                module,
                "demo_account_emails",
                return_value=frozenset({demo_account.RETAIL_EMAIL}),
            ),
            mock.patch(
                "extra_settings.models.Setting.get",
                side_effect=lambda name, default=None: {
                    "CONTACT_EMAIL": "hello@demo.grooveshop.space",
                    "INVOICE_SELLER_EMAIL": "hello@demo.grooveshop.space",
                }.get(name, default),
            ),
        ):
            mailboxes = module.demo_store_mailboxes()

        self.assertIn(demo_account.RETAIL_EMAIL, mailboxes)
        self.assertIn("hello@demo.grooveshop.space", mailboxes)

    def test_is_empty_on_a_store_with_no_demo_account(self):
        # The gate is the same one that publishes the credentials, so an
        # ordinary merchant's mail is never inspected.
        from core import demo_account as module

        with mock.patch.object(
            module, "demo_account_emails", return_value=frozenset()
        ):
            self.assertEqual(module.demo_store_mailboxes(), frozenset())

    def test_the_published_address_is_not_treated_as_a_login(self):
        """`is_demo_account` must keep meaning "a shared login".

        Widening it would refuse a password change to any customer whose
        address happened to match the store's published one.
        """
        from core import demo_account as module

        with mock.patch.object(
            module,
            "demo_account_emails",
            return_value=frozenset({demo_account.RETAIL_EMAIL}),
        ):
            shopper = SimpleNamespace(email="hello@demo.grooveshop.space")
            self.assertFalse(module.is_demo_account(shopper))


class TestRicherAccountDataset(TestCase):
    """The shape of the shopper's history, without a database."""

    def test_fourteen_orders_across_the_statuses_a_shop_has(self):
        assert len(demo_account.ORDERS) == 14
        statuses = {order.status for order in demo_account.ORDERS}
        assert {
            "PROCESSING",
            "SHIPPED",
            "COMPLETED",
            "CANCELED",
            "RETURNED",
            "REFUNDED",
        } <= statuses

    def test_every_order_that_shipped_carries_a_carrier_row(self):
        for order in demo_account.ORDERS + demo_account.WHOLESALE_ORDERS:
            if order.status in {"SHIPPED", "RETURNED", "REFUNDED"}:
                assert order.shipment is not None, order
        carriers = {
            order.shipment.carrier
            for order in demo_account.ORDERS
            if order.shipment
        }
        assert carriers == {"acs", "boxnow"}

    def test_a_shipment_ends_in_a_state_its_order_can_hold(self):
        delivered = {"delivered"}
        for order in demo_account.ORDERS + demo_account.WHOLESALE_ORDERS:
            shipment = order.shipment
            if shipment is None:
                continue
            if order.status == "COMPLETED":
                assert shipment.state in delivered, order
            if order.status == "SHIPPED":
                assert shipment.state not in delivered | {"returned"}, order
            if order.status in {"RETURNED", "REFUNDED"}:
                assert shipment.state == "returned", order

    def test_vouchers_are_unique_and_unmistakably_fake(self):
        vouchers = [
            order.shipment.voucher
            for order in demo_account.ORDERS + demo_account.WHOLESALE_ORDERS
            if order.shipment
        ]
        assert len(vouchers) == len(set(vouchers))
        for voucher in vouchers:
            assert voucher.startswith("DEMO"), voucher
            assert len(voucher) <= 20, "AcsShipment.voucher_no is 20 wide"

    def test_in_flight_parcels_are_the_newest_so_tracking_stays_fresh(self):
        from devtools.demo_shipments import FINAL_STATES

        newest_final = min(
            order.days_ago
            for order in demo_account.ORDERS
            if order.shipment and order.shipment.state in FINAL_STATES
        )
        for order in demo_account.ORDERS:
            if order.shipment and order.shipment.state not in FINAL_STATES:
                assert order.days_ago < newest_final, order

    def test_a_cash_order_is_courier_cash_and_a_locker_order_is_boxnow(self):
        for order in demo_account.ORDERS:
            if order.shipment and order.shipment.carrier == "boxnow":
                assert order.settlement in {"online", "carrier_terminal"}
                assert order.shipment.locker_id
            if order.settlement == "courier_cash":
                assert order.shipment and order.shipment.carrier == "acs"

    def test_there_is_a_cyprus_address_and_a_cyprus_order(self):
        assert {a.country for a in demo_account.ADDRESSES} == {"GR", "CY"}
        cyprus = [o for o in demo_account.ORDERS if o.country == "CY"]
        assert cyprus
        for order in cyprus:
            assert order.shipment and order.shipment.destination_country == "CY"

    def test_one_order_redeems_a_coupon_and_one_spends_the_gift_card(self):
        assert sum(1 for o in demo_account.ORDERS if o.promotion_code) == 1
        assert sum(1 for o in demo_account.ORDERS if o.gift_card) == 1
        assert demo_account.GIFT_CARD_SPENT < demo_account.GIFT_CARD_AMOUNT
        assert (
            demo_account.GIFT_CARD_BALANCE
            == demo_account.GIFT_CARD_AMOUNT - demo_account.GIFT_CARD_SPENT
        )

    def test_the_points_ledger_makes_the_account_silver(self):
        kinds = {row.transaction_type for row in demo_account.POINTS}
        assert {"BONUS", "EARN", "REDEEM"} <= kinds
        xp = sum(r.points for r in demo_account.POINTS if r.points > 0)
        balance = sum(r.points for r in demo_account.POINTS)
        assert xp >= 4000, "level 5 is where Silver starts"
        assert 0 < balance < xp
        descriptions = [
            (r.transaction_type, r.description) for r in demo_account.POINTS
        ]
        orders = {o.days_ago for o in demo_account.ORDERS}
        for row in demo_account.POINTS:
            if row.order is not None:
                assert row.order in orders, row
        assert len({d for d in descriptions if d[0] != "EARN"}) == len(
            [d for d in descriptions if d[0] != "EARN"]
        )

    def test_five_notifications_some_read(self):
        from notification.enum import (
            NotificationCategoryEnum,
            NotificationKindEnum,
            NotificationTypeEnum,
        )

        rows = demo_account.NOTIFICATIONS
        assert len(rows) == 5
        assert {r.seen for r in rows} == {True, False}
        orders = {o.days_ago for o in demo_account.ORDERS}
        for row in rows:
            assert row.notification_type in NotificationTypeEnum.values
            assert row.kind in NotificationKindEnum.values
            assert row.category in NotificationCategoryEnum.values
            assert row.title_el.strip() and row.title_en.strip()
            assert row.message_el.strip() and row.message_en.strip()
            if row.order is not None:
                assert row.order in orders, row
            else:
                assert row.link.startswith("/"), row

    def test_the_wholesale_business_is_clearly_fake_but_passes_the_checksum(
        self,
    ):
        from b2b.validators import is_valid_greek_vat

        assert is_valid_greek_vat(demo_account.WHOLESALE_VAT_ID)
        assert set(demo_account.WHOLESALE_VAT_ID[:-1]) == {"9"}
        assert demo_account.WHOLESALE_ORDERS
        for order in demo_account.WHOLESALE_ORDERS:
            assert order.settlement in {"offline_transfer", "online"}
            assert all(qty >= 6 for _slug, qty in order.items)
        days = [o.days_ago for o in demo_account.WHOLESALE_ORDERS]
        assert len(days) == len(set(days))
        slugs = {row.slug for row in PRODUCTS}
        for order in demo_account.WHOLESALE_ORDERS:
            for slug, _qty in order.items:
                assert slug in slugs, slug

    def test_the_wholesale_login_is_the_one_the_store_already_publishes(self):
        """No second credential: the profile hangs off the shared login
        ``showcase_settings`` already advertises."""
        values = demo_account.showcase_settings()
        assert values["DEMO_ACCOUNT_B2B_EMAIL"] == demo_account.B2B_EMAIL
        assert values["DEMO_ACCOUNT_B2B_PASSWORD"] == demo_account.B2B_PASSWORD


@pytest.fixture(autouse=True)
def _no_pdf_engine():
    """The invoice renderer needs the system libraries WeasyPrint wraps,
    which a developer machine may not have. Everything around the render
    (numbering, snapshots, the context) still runs for real."""
    with mock.patch(
        "order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 demo"
    ):
        yield


def _prepare_store():
    """Products, pay ways and the coupon the fixtures need."""
    from djmoney.money import Money

    from pay_way.enum.settlement import PaySettlement
    from pay_way.factories import PayWayFactory
    from pay_way.models import PayWay
    from product.factories.product import ProductFactory
    from promotion.enum import BenefitType
    from promotion.factories.promotion import (
        PromotionCodeFactory,
        PromotionFactory,
    )

    slugs = {
        slug
        for row in demo_account.ORDERS + demo_account.WHOLESALE_ORDERS
        for slug, _qty in row.items
    }
    for slug in sorted(slugs):
        ProductFactory(slug=slug, stock=100, num_images=0, num_reviews=0)
    wanted = {
        PaySettlement.ONLINE.value: PayWayFactory.create_online_payment,
        PaySettlement.CARRIER_TERMINAL.value: lambda: PayWayFactory(
            settlement=PaySettlement.CARRIER_TERMINAL.value
        ),
        PaySettlement.COURIER_CASH.value: lambda: (
            PayWayFactory.create_offline_payment(requires_confirmation=False)
        ),
        PaySettlement.OFFLINE_TRANSFER.value: (
            PayWayFactory.create_offline_payment
        ),
    }
    for settlement, make in wanted.items():
        if not PayWay.objects.filter(settlement=settlement).exists():
            make()
    PromotionCodeFactory(
        code="SAVE5",
        promotion=PromotionFactory(
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal("5.00"),
            min_subtotal=Money("25.00", "EUR"),
        ),
    )


@pytest.mark.django_db
class TestSeededAccount:
    """What ``seed_demo_account`` writes for the shopper and the buyer."""

    @pytest.fixture(autouse=True)
    def _a_demo_store(self, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        with mock.patch(
            "devtools.demo_store._current_tenant_is_demo", return_value=True
        ):
            yield

    @pytest.fixture
    def seeded(self):
        _prepare_store()
        return demo_account.seed_demo_account()

    @staticmethod
    def _retail():
        from django.contrib.auth import get_user_model

        return get_user_model().objects.get(email=demo_account.RETAIL_EMAIL)

    def test_the_shopper_has_fourteen_orders_in_their_states(self, seeded):
        from order.models.order import Order

        assert seeded["orders"] == 14
        orders = Order.objects.filter(user=self._retail())
        assert orders.count() == 14
        assert {o.status for o in orders} == {
            row.status for row in demo_account.ORDERS
        }

    def test_orders_total_what_the_shopper_owed(self, seeded):
        from order.models.order import Order

        for order in Order.objects.filter(user=self._retail()):
            assert order.paid_amount.amount > 0, order.pk
            assert order.paid_amount == order.calculate_order_total_amount()
            assert order._payment_consistency_errors() == {}

    def test_shipments_carry_the_state_events_and_tracking_number(self, seeded):
        from shipping_acs.models import AcsShipment
        from shipping_boxnow.models import BoxNowShipment

        rows = {r.days_ago: r for r in demo_account.ORDERS if r.shipment}
        acs = AcsShipment.objects.filter(order__user=self._retail())
        boxnow = BoxNowShipment.objects.filter(order__user=self._retail())
        assert acs.count() == sum(
            1 for r in rows.values() if r.shipment.carrier == "acs"
        )
        assert boxnow.count() == sum(
            1 for r in rows.values() if r.shipment.carrier == "boxnow"
        )
        for shipment in acs:
            row = rows[shipment.order.metadata["demo_seed"]]
            assert shipment.voucher_no == row.shipment.voucher
            assert shipment.shipment_state == row.shipment.state
            assert shipment.order.tracking_number == shipment.voucher_no
            assert shipment.order.shipping_carrier == "acs"
            assert shipment.events.count() >= 1
            assert shipment.last_event_at == max(
                e.event_time for e in shipment.events.all()
            )
        for shipment in boxnow:
            row = rows[shipment.order.metadata["demo_seed"]]
            assert shipment.parcel_id == row.shipment.voucher
            assert shipment.parcel_state == row.shipment.state
            assert shipment.locker_external_id == row.shipment.locker_id
            assert shipment.events.count() >= 2

    def test_an_acs_voucher_collects_cash_only_on_a_cash_order(self, seeded):
        from shipping_acs.enum.charge_type import AcsChargeType
        from shipping_acs.models import AcsShipment

        for shipment in AcsShipment.objects.filter(order__user=self._retail()):
            assert shipment.charge_type == AcsChargeType.COD
            if shipment.order.pay_way.settlement == "courier_cash":
                assert shipment.cod_amount == shipment.order.paid_amount
                assert shipment.delivery_products == "COD"
            else:
                assert shipment.cod_amount.amount == 0
                assert shipment.delivery_products == ""

    def test_no_shipment_is_stale_or_pollable_into_the_past(self, seeded):
        """``check_stale_acs_shipments`` emails the merchant about a
        non-terminal voucher with no recent event, and the pollers pick
        up anything not polled in 15 minutes — so what is still in
        flight reports within the last day, and finished rows are
        terminal."""
        from shipping_acs.models import AcsShipment
        from shipping_boxnow.models import BoxNowShipment

        cutoff = timezone.now() - timedelta(
            days=django_settings.ACS_STALE_SHIPMENT_DAYS
        )
        for model in (AcsShipment, BoxNowShipment):
            for shipment in model.objects.filter(order__user=self._retail()):
                if shipment.is_active:
                    assert shipment.last_event_at > cutoff, shipment
                else:
                    assert shipment.last_event_at <= timezone.now()

    def test_cyprus_address_and_order(self, seeded):
        from order.models.order import Order
        from user.models.address import UserAddress

        address = UserAddress.objects.get(
            user=self._retail(), country__alpha_2="CY"
        )
        assert address.city == "Λευκωσία"
        order = Order.objects.get(user=self._retail(), country__alpha_2="CY")
        assert order.city == address.city
        assert order.shipping_kind == "pickup_point"

    def test_one_coupon_is_redeemed_and_priced_into_the_order(self, seeded):
        from promotion.models.redemption import PromotionRedemption

        redemption = PromotionRedemption.objects.get(user=self._retail())
        assert redemption.code.code == "SAVE5"
        assert redemption.amount.amount == Decimal("5.00")
        assert redemption.order.discount_amount == redemption.amount
        assert redemption.order.metadata["promotions"][0]["code"] == "SAVE5"

    def test_the_account_is_silver(self, seeded):
        from loyalty.models.transaction import PointsTransaction
        from loyalty.services import LoyaltyService

        user = self._retail()
        user.refresh_from_db()
        assert user.loyalty_tier is not None
        assert user.loyalty_tier.safe_translation_getter(
            "name", language_code="en", any_language=True
        ).lower() in {"silver", "ασημένιο"}
        assert LoyaltyService.get_user_level(user) >= 5
        balance = PointsTransaction.objects.get_balance(user)
        assert balance == sum(r.points for r in demo_account.POINTS)
        assert PointsTransaction.objects.filter(
            user=user, reference_order__isnull=False
        ).exists()

    def test_five_notifications_three_unread(self, seeded):
        from notification.models import NotificationUser

        links = NotificationUser.objects.filter(user=self._retail())
        assert links.count() == 5
        assert links.filter(seen=False).count() == 3
        for link in links.select_related("notification"):
            assert link.notification.get_translation("el").title
            assert link.notification.get_translation("en").title

    def test_the_gift_card_is_part_spent_and_linked_to_its_order(self, seeded):
        from giftcard.enum import GiftCardTransactionKind
        from giftcard.models import GiftCard

        card = GiftCard.objects.get(code=demo_account.GIFT_CARD_CODE)
        assert card.balance.amount == demo_account.GIFT_CARD_BALANCE
        spend = card.transactions.get(kind=GiftCardTransactionKind.REDEEM)
        assert spend.amount == -demo_account.GIFT_CARD_SPENT
        assert spend.order is not None
        assert spend.order.gift_card_amount.amount == (
            demo_account.GIFT_CARD_SPENT
        )

    def test_invoices_have_a_number_a_document_and_the_orders_date(
        self, seeded
    ):
        from order.models.invoice import Invoice

        wanted = sum(1 for r in demo_account.ORDERS if r.invoice)
        invoices = Invoice.objects.filter(order__user=self._retail())
        assert invoices.count() == wanted
        for invoice in invoices.select_related("order"):
            assert invoice.has_document()
            assert invoice.invoice_number.startswith("INV-")
            assert invoice.issue_date <= timezone.localdate()
            assert (
                invoice.issue_date
                <= (invoice.order.created_at + timedelta(days=1)).date()
            )
            assert invoice.mydata_status == "NOT_SENT"
            assert invoice.total.amount > 0

    def test_the_wholesale_account_is_an_approved_business_on_group_prices(
        self, seeded
    ):
        from django.contrib.auth import get_user_model

        from b2b.enum import BusinessProfileStatus
        from b2b.models import BusinessProfile
        from b2b.services import B2BPricingService
        from order.models.invoice import Invoice
        from order.models.order import Order

        buyer = get_user_model().objects.get(email=demo_account.B2B_EMAIL)
        profile = BusinessProfile.objects.get(user=buyer)
        assert profile.status == BusinessProfileStatus.APPROVED
        assert profile.customer_group is not None
        assert profile.vat_id == demo_account.WHOLESALE_VAT_ID
        assert profile.reviewed_at is not None

        orders = Order.objects.filter(user=buyer)
        assert orders.count() == len(demo_account.WHOLESALE_ORDERS)
        for order in orders:
            assert order.document_type == "INVOICE"
            assert order.billing_vat_id == demo_account.WHOLESALE_VAT_ID
            assert order.billing_company_name == profile.company_name
            assert order.metadata["b2b_pricing"]["group_id"] == (
                profile.customer_group.pk
            )
            assert order.paid_amount == order.calculate_order_total_amount()
            for item in order.items.select_related("product"):
                priced = B2BPricingService.resolve_single(
                    item.product, profile.customer_group
                )
                assert item.price == priced.final
        assert Invoice.objects.filter(order__user=buyer).count() == sum(
            1 for r in demo_account.WHOLESALE_ORDERS if r.invoice
        )

    def test_a_second_run_writes_nothing(self, seeded):
        from loyalty.models.transaction import PointsTransaction
        from notification.models import Notification
        from order.models.invoice import Invoice
        from order.models.order import Order
        from shipping_acs.models import AcsShipment

        before = (
            Order.objects.count(),
            Invoice.objects.count(),
            AcsShipment.objects.count(),
            PointsTransaction.objects.count(),
            Notification.objects.count(),
        )
        report = demo_account.seed_demo_account()

        for key in (
            "orders",
            "b2b_orders",
            "shipments",
            "invoices",
            "points",
            "notifications",
            "redemptions",
            "gift_card_spend",
            "b2b_shipments",
            "b2b_invoices",
            "gift_card",
        ):
            assert not report.get(key), (key, report)
        assert (
            Order.objects.count(),
            Invoice.objects.count(),
            AcsShipment.objects.count(),
            PointsTransaction.objects.count(),
            Notification.objects.count(),
        ) == before

    def test_seeding_is_silent(
        self, django_capture_on_commit_callbacks, settings
    ):
        """No mail, no dispatched task, no WebSocket push, no carrier,
        payment or myDATA request: every row is built directly."""
        from unittest import mock

        from django.core import mail

        _prepare_store()
        mail.outbox = []
        with (
            mock.patch("celery.app.task.Task.apply_async") as dispatched,
            mock.patch("requests.sessions.Session.send") as http,
            mock.patch(
                "urllib3.connectionpool.HTTPConnectionPool.urlopen"
            ) as raw,
            django_capture_on_commit_callbacks(execute=True) as callbacks,
        ):
            demo_account.seed_demo_account()

        assert mail.outbox == []
        dispatched.assert_not_called()
        http.assert_not_called()
        raw.assert_not_called()
        # Nothing was left to run when a transaction committed either.
        assert callbacks == []

    def test_no_order_history_row_means_no_signal_fired(self, seeded):
        from order.models.history import OrderHistory
        from order.models.order import Order

        assert not OrderHistory.objects.filter(
            order__in=Order.objects.filter(user=self._retail())
        ).exists()


@pytest.mark.django_db
class TestResetRebuildsTheAccounts:
    """The nightly reset wipes what a visitor can change and rebuilds it."""

    @pytest.fixture(autouse=True)
    def _a_demo_store(self, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        with mock.patch(
            "devtools.demo_store._current_tenant_is_demo", return_value=True
        ):
            yield

    @staticmethod
    def _counts():
        from loyalty.models.transaction import PointsTransaction
        from notification.models import Notification, NotificationUser
        from order.models.invoice import Invoice
        from order.models.order import Order
        from promotion.models.redemption import PromotionRedemption
        from shipping_acs.models import AcsShipment
        from shipping_boxnow.models import BoxNowShipment

        return {
            "orders": Order.objects.all_with_deleted().count(),
            "invoices": Invoice.objects.count(),
            "acs": AcsShipment.objects.count(),
            "boxnow": BoxNowShipment.objects.count(),
            "points": PointsTransaction.objects.count(),
            "notifications": Notification.objects.count(),
            "notification_users": NotificationUser.objects.count(),
            "redemptions": PromotionRedemption.objects.count(),
        }

    def test_reset_converges_and_never_duplicates(
        self, django_capture_on_commit_callbacks
    ):
        from django.core import mail

        _prepare_store()
        demo_account.seed_demo_account()
        first = self._counts()

        mail.outbox = []
        with (
            mock.patch("celery.app.task.Task.apply_async") as dispatched,
            django_capture_on_commit_callbacks(execute=True),
        ):
            demo_account.reset_demo_account()
            demo_account.reset_demo_account()

        assert self._counts() == first
        assert mail.outbox == []
        dispatched.assert_not_called()

    def test_a_visitors_changes_are_wiped_and_the_history_comes_back(self):
        from django.contrib.auth import get_user_model

        from notification.models import Notification, NotificationUser
        from order.models.order import Order
        from product.models import Product, ProductReview

        _prepare_store()
        demo_account.seed_demo_account()
        user = get_user_model().objects.get(email=demo_account.RETAIL_EMAIL)
        before = set(
            Order.objects.filter(user=user).values_list("pk", flat=True)
        )
        ProductReview.objects.create(
            product=Product.objects.first(), user=user, rate=2
        )
        visitor = Notification(kind="INFO")
        visitor.set_current_language("el")
        visitor.title, visitor.message = "x", "y"
        visitor.save()
        NotificationUser.objects.bulk_create(
            [NotificationUser(user=user, notification=visitor)]
        )

        report = demo_account.reset_demo_account()

        assert report["seeded_orders"] == len(demo_account.ORDERS)
        after = set(
            Order.objects.filter(user=user).values_list("pk", flat=True)
        )
        assert len(after) == len(demo_account.ORDERS)
        assert not (before & after), "the orders are rebuilt, not kept"
        assert not ProductReview.objects.filter(user=user).exists()
        assert not Notification.objects.filter(pk=visitor.pk).exists()
        assert NotificationUser.objects.filter(user=user).count() == 5

    def test_the_invoice_numbers_do_not_climb_night_after_night(self):
        """The wipe gives the numbers it frees back to the counter, so the
        sequence is the same after a hundred nights as after one."""
        from order.models.invoice import Invoice, InvoiceCounter

        _prepare_store()
        demo_account.seed_demo_account()
        numbers = sorted(
            Invoice.objects.values_list("invoice_number", flat=True)
        )

        for _night in range(2):
            demo_account.reset_demo_account()

        assert (
            sorted(Invoice.objects.values_list("invoice_number", flat=True))
            == numbers
        )
        counter = InvoiceCounter.objects.get()
        assert counter.next_number == len(numbers) + 1

    def test_wiping_an_invoice_removes_its_pdf_as_well_as_its_row(self):
        from order.models.invoice import Invoice
        from order.models.order import Order

        _prepare_store()
        demo_account.seed_demo_account()
        invoices = list(Invoice.objects.all())
        storage = invoices[0].document_file.storage
        paths = [invoice.document_file.name for invoice in invoices]
        assert paths and all(storage.exists(path) for path in paths)

        removed = demo_account._delete_invoices(
            list(Order.objects.values_list("pk", flat=True))
        )

        assert removed == len(invoices)
        assert not Invoice.objects.exists()
        assert not any(storage.exists(path) for path in paths)

    def test_the_part_spent_card_stays_part_spent_without_ledger_growth(self):
        from giftcard.models import GiftCard

        _prepare_store()
        demo_account.seed_demo_account()
        card = GiftCard.objects.get(code=demo_account.GIFT_CARD_CODE)
        rows = card.transactions.count()

        for _night in range(3):
            demo_account.reset_demo_account()

        card.refresh_from_db()
        assert card.balance.amount == demo_account.GIFT_CARD_BALANCE
        assert card.transactions.count() == rows
        assert card.transactions.filter(order__isnull=False).count() == 1

    def test_the_wholesale_profile_survives_the_reset(self):
        from b2b.models import BusinessProfile
        from order.models.order import Order

        _prepare_store()
        demo_account.seed_demo_account()
        pk = BusinessProfile.objects.get(user__email=demo_account.B2B_EMAIL).pk

        demo_account.reset_demo_account()

        profile = BusinessProfile.objects.get(
            user__email=demo_account.B2B_EMAIL
        )
        assert profile.pk == pk
        assert profile.status == "APPROVED"

        assert Order.objects.filter(user=profile.user).count() == len(
            demo_account.WHOLESALE_ORDERS
        )

    def test_the_seed_step_ends_where_the_reset_does(self):
        """``seed_demo_store`` and the nightly task must converge: the
        step runs the reset, so an order an older dataset wrote (no
        carrier row, no invoice) is rebuilt rather than kept."""
        from django.contrib.auth import get_user_model

        from devtools import demo_store
        from order.enum.status import OrderStatus, PaymentStatus
        from order.factories.order import OrderFactory
        from order.models.order import Order
        from shipping_acs.models import AcsShipment

        _prepare_store()
        retail = get_user_model().objects.create(
            email=demo_account.RETAIL_EMAIL, is_active=True
        )
        stale = OrderFactory(
            user=retail,
            status=OrderStatus.COMPLETED,
            payment_status=PaymentStatus.COMPLETED,
            num_order_items=1,
            metadata={"demo_seed": 21},
        )

        report = demo_store.seed_demo_account()

        assert report["seeded_orders"] == len(demo_account.ORDERS)
        assert not Order.objects.filter(pk=stale.pk).exists()
        assert Order.objects.filter(user=retail).count() == len(
            demo_account.ORDERS
        )
        assert AcsShipment.objects.filter(order__user=retail).exists()
        again = demo_store.seed_demo_account()
        assert again["seeded_orders"] == len(demo_account.ORDERS)
        assert Order.objects.filter(user=retail).count() == len(
            demo_account.ORDERS
        )

    def test_a_demo_login_still_works_after_the_reset(self):
        from django.contrib.auth import get_user_model

        _prepare_store()
        demo_account.seed_demo_account()
        demo_account.reset_demo_account()

        for email, password in (
            (demo_account.RETAIL_EMAIL, demo_account.RETAIL_PASSWORD),
            (demo_account.B2B_EMAIL, demo_account.B2B_PASSWORD),
        ):
            user = get_user_model().objects.get(email=email)
            assert user.check_password(password)


@pytest.mark.django_db
class TestResetIsSilent:
    """The nightly reset rebuilds fixtures; it must not act like a sale.

    Production 2026-09-25 04:00: every fixture order went through
    ``save()``, so ``order_created`` fired four times — four order
    confirmations and four merchant new-order emails (the demo mail
    backend dropped them), a WebSocket toast and a gateway push each —
    and the queryset "wipe" only soft-deleted, leaving 12 hidden rows.
    """

    @pytest.fixture(autouse=True)
    def _a_demo_store(self):
        # The reset refuses a schema that is not flagged ``is_demo``;
        # the test schema is not a tenant at all.
        with mock.patch(
            "devtools.demo_store._current_tenant_is_demo", return_value=True
        ):
            yield

    @staticmethod
    def _catalogue():
        _prepare_store()

    def test_reset_sends_no_mail_dispatches_nothing_leaves_no_residue(
        self, settings, django_capture_on_commit_callbacks
    ):
        from django.core import mail

        from order.models.history import OrderHistory
        from order.models.order import Order

        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        self._catalogue()
        demo_account.seed_demo_account()

        mail.outbox = []
        with (
            mock.patch("celery.app.task.Task.apply_async") as dispatched,
            django_capture_on_commit_callbacks(execute=True),
        ):
            demo_account.reset_demo_account()

        assert mail.outbox == []
        dispatched.assert_not_called()

        orders = Order.objects.all_with_deleted().filter(
            user__email=demo_account.RETAIL_EMAIL
        )
        assert orders.count() == len(demo_account.ORDERS)
        assert not orders.filter(is_deleted=True).exists()
        # ``handle_order_created`` writes "Order created" into the
        # history; no row at all proves the signal never fired.
        assert not OrderHistory.objects.filter(order__in=orders).exists()

    def test_fixtures_are_internally_consistent(self, settings):
        from order.models.order import Order

        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        self._catalogue()
        demo_account.seed_demo_account()

        by_seed = {
            order.metadata["demo_seed"]: order
            for order in Order.objects.filter(
                user__email=demo_account.RETAIL_EMAIL
            ).select_related("pay_way")
        }
        for row in demo_account.ORDERS:
            order = by_seed[row.days_ago]
            assert order.pay_way.settlement == row.settlement
            assert order.pay_way_key
            assert order.paid_amount.amount > 0
            assert order.paid_amount == order.calculate_order_total_amount()
            if row.payment_status in {"COMPLETED", "REFUNDED"}:
                assert order.payment_method == order.pay_way.provider_code
            else:
                assert order.payment_method == ""
            # The model's own payment rule accepts every fixture.
            assert order._payment_consistency_errors() == {}


@pytest.mark.django_db
class TestGuestCheckoutsAreCleared:
    """A visitor's guest checkout on the demo store goes with the reset.

    Prod demo order #1 (2026-09-22) was a guest cash-on-delivery order on
    a store with no carrier: nothing could advance it, the auto-cancel
    only closes online payments, and the account wipe never reached it.
    """

    @staticmethod
    def _guest_order_holding_stock():
        from order.enum.status import OrderStatus, PaymentStatus
        from order.factories.order import OrderFactory
        from order.models.stock_reservation import StockReservation
        from order.stock import StockManager
        from product.factories.product import ProductFactory

        # Outside the seeded catalogue, so ``_restore_demo_stock`` cannot
        # be what puts the stock back.
        product = ProductFactory(
            slug=f"visitor-{uuid.uuid4().hex[:8]}",
            stock=10,
            num_images=0,
            num_reviews=0,
        )
        order = OrderFactory(
            user=None,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            num_order_items=0,
        )
        StockManager.decrement_stock(
            product_id=product.id, quantity=2, order_id=order.id
        )
        reservation = StockReservation.objects.create(
            product=product,
            quantity=1,
            session_id="visitor",
            expires_at=timezone.now() + timedelta(minutes=15),
            order=order,
        )
        return order, product, reservation

    def test_the_guest_order_goes_and_its_stock_comes_back(
        self, settings, django_capture_on_commit_callbacks
    ):
        from django.core import mail

        from order.models.order import Order

        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        order, product, reservation = self._guest_order_holding_stock()
        product.refresh_from_db()
        assert product.stock == 8
        TestResetIsSilent._catalogue()
        demo_account.seed_demo_account()

        mail.outbox = []
        with (
            mock.patch(
                "devtools.demo_store._current_tenant_is_demo",
                return_value=True,
            ),
            mock.patch("celery.app.task.Task.apply_async") as dispatched,
            django_capture_on_commit_callbacks(execute=True),
        ):
            report = demo_account.reset_demo_account()

        assert report["guest_orders"] == 1
        assert report["guest_stock_restored"] == 2
        assert not Order.objects.all_with_deleted().filter(pk=order.pk).exists()
        product.refresh_from_db()
        assert product.stock == 10
        reservation.refresh_from_db()
        assert reservation.consumed is True
        assert mail.outbox == []
        dispatched.assert_not_called()
        # The shared account's fixtures are untouched.
        assert Order.objects.filter(
            user__email=demo_account.RETAIL_EMAIL
        ).count() == len(demo_account.ORDERS)

    def test_restocking_a_sold_out_product_emails_nobody(
        self, settings, django_capture_on_commit_callbacks
    ):
        """A guest who bought the last units leaves the product at 0; the
        reset puts them back WITHOUT the 0 → positive save that fires
        ``product_back_in_stock`` (restock emails to every subscriber)."""
        from django.core import mail

        from order.enum.status import OrderStatus, PaymentStatus
        from order.factories.order import OrderFactory
        from order.models.stock_log import StockLog
        from order.stock import StockManager
        from product.factories.product import ProductFactory

        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        product = ProductFactory(
            slug=f"sold-out-{uuid.uuid4().hex[:8]}",
            stock=2,
            num_images=0,
            num_reviews=0,
        )
        order = OrderFactory(
            user=None,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            num_order_items=0,
        )
        StockManager.decrement_stock(
            product_id=product.id, quantity=2, order_id=order.id
        )

        mail.outbox = []
        with (
            mock.patch("celery.app.task.Task.apply_async") as dispatched,
            django_capture_on_commit_callbacks(execute=True),
        ):
            demo_account._wipe_guest_checkouts(before=timezone.now())

        product.refresh_from_db()
        assert product.stock == 2
        dispatched.assert_not_called()
        assert mail.outbox == []
        # The restore is still on the audit trail.
        assert StockLog.objects.filter(
            product=product,
            operation_type=StockLog.OPERATION_INCREMENT,
            quantity_delta=2,
        ).exists()

    def test_an_order_placed_after_the_run_began_is_kept(self):
        from order.models.order import Order

        order, _product, _reservation = self._guest_order_holding_stock()

        demo_account._wipe_guest_checkouts(
            before=order.created_at - timedelta(seconds=1)
        )

        assert Order.objects.filter(pk=order.pk).exists()

    def test_refuses_to_run_on_a_store_that_is_not_a_demo(self):
        from order.models.order import Order

        order, _product, _reservation = self._guest_order_holding_stock()

        with mock.patch(
            "devtools.demo_store._current_tenant_is_demo", return_value=False
        ):
            report = demo_account.reset_demo_account()

        assert report == {"skipped_not_a_demo_tenant": 1}
        assert Order.objects.filter(pk=order.pk).exists()

    def test_the_demo_check_reads_the_tenant_flag(self):
        """The guard itself: a non-demo, non-public schema is refused."""
        from devtools.demo_store import _current_tenant_is_demo

        # The test database has no Tenant row flagged ``is_demo`` for
        # whatever schema it runs in.
        assert _current_tenant_is_demo() is False


@pytest.mark.django_db
class TestDemoGiftCardIsRestored:
    """Guests on the demo store may spend the published gift card, and
    the seed never re-issues it; the reset returns it to its issued
    balance through the ledger, never by rewriting history."""

    @pytest.fixture(autouse=True)
    def _a_demo_store(self, settings):
        settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
        with mock.patch(
            "devtools.demo_store._current_tenant_is_demo", return_value=True
        ):
            yield

    @staticmethod
    def _seeded_card():
        from giftcard.models import GiftCard

        TestResetIsSilent._catalogue()
        demo_account.seed_demo_account()
        return GiftCard.objects.get(code=demo_account.GIFT_CARD_CODE)

    @staticmethod
    def _spend(card, amount: str):
        """What a guest checkout leaves: a REDEEM row on a guest order
        that the reset then deletes."""
        from giftcard.enum import GiftCardTransactionKind
        from giftcard.models import GiftCardTransaction
        from order.enum.status import OrderStatus, PaymentStatus
        from order.factories.order import OrderFactory

        order = OrderFactory(
            user=None,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            num_order_items=0,
        )
        GiftCardTransaction.objects.create(
            gift_card=card,
            kind=GiftCardTransactionKind.REDEEM,
            amount=-Decimal(amount),
            order=order,
            description=f"Order #{order.id}",
        )
        return order

    @staticmethod
    def _ledger_sum(card):
        from django.db.models import Sum

        return card.transactions.aggregate(total=Sum("amount"))["total"]

    def test_a_spent_card_is_back_to_its_issued_balance(
        self, django_capture_on_commit_callbacks
    ):
        from django.core import mail

        from giftcard.enum import GiftCardStatus, GiftCardTransactionKind

        card = self._seeded_card()
        order = self._spend(card, "10.00")
        assert card.balance.amount == demo_account.GIFT_CARD_BALANCE - Decimal(
            "10.00"
        )

        mail.outbox = []
        with (
            mock.patch("celery.app.task.Task.apply_async") as dispatched,
            django_capture_on_commit_callbacks(execute=True),
        ):
            report = demo_account.reset_demo_account()

        card.refresh_from_db()
        assert report["gift_card_restored"] == 1
        assert card.balance.amount == demo_account.GIFT_CARD_BALANCE
        # The ledger IS the balance: nothing was overwritten.
        assert self._ledger_sum(card) == demo_account.GIFT_CARD_BALANCE
        assert card.is_redeemable
        assert card.status == GiftCardStatus.ACTIVE
        # The spend stays on the card's history, its guest order gone.
        redeem = card.transactions.get(
            kind=GiftCardTransactionKind.REDEEM, order__isnull=True
        )
        assert redeem.amount == -Decimal("10.00")
        assert (
            not type(order)
            .objects.all_with_deleted()
            .filter(pk=order.pk)
            .exists()
        )
        adjust = card.transactions.get(kind=GiftCardTransactionKind.ADJUST)
        assert str(adjust.amount) == "10.00"
        assert mail.outbox == []
        dispatched.assert_not_called()

    def test_a_disabled_and_expired_card_is_active_again(self):
        from giftcard.enum import GiftCardStatus
        from giftcard.models import GiftCard
        from giftcard.services import GiftCardService

        card = self._seeded_card()
        GiftCard.objects.filter(pk=card.pk).update(
            status=GiftCardStatus.DISABLED,
            expires_at=timezone.now() - timedelta(days=1),
            expiry_reminder_sent_at=timezone.now() - timedelta(days=30),
        )
        # The daily sweep reclaims an expired card's balance.
        GiftCardService.expire_cards()

        demo_account.reset_demo_account()

        card.refresh_from_db()
        assert card.status == GiftCardStatus.ACTIVE
        assert not card.is_expired
        assert card.expiry_reminder_sent_at is None
        assert card.balance.amount == demo_account.GIFT_CARD_BALANCE
        assert self._ledger_sum(card) == demo_account.GIFT_CARD_BALANCE
        assert card.is_redeemable

    def test_an_untouched_card_gets_no_ledger_row(self):
        from giftcard.enum import GiftCardTransactionKind

        card = self._seeded_card()

        report = demo_account.reset_demo_account()

        assert report["gift_card_restored"] == 0
        assert not card.transactions.filter(
            kind=GiftCardTransactionKind.ADJUST
        ).exists()
        assert self._ledger_sum(card) == demo_account.GIFT_CARD_BALANCE

    def test_a_card_the_seed_did_not_issue_is_left_alone(self):
        from djmoney.money import Money

        from giftcard.services import GiftCardService

        self._seeded_card()
        bought = GiftCardService.issue(Money("30.00", "EUR"))
        self._spend(bought, "30.00")

        demo_account.reset_demo_account()

        bought.refresh_from_db()
        assert bought.balance.amount == 0
        assert bought.transactions.count() == 2

    def test_a_store_that_is_not_a_demo_is_untouched(self):
        card = self._seeded_card()
        self._spend(card, "25.00")

        with mock.patch(
            "devtools.demo_store._current_tenant_is_demo", return_value=False
        ):
            report = demo_account.reset_demo_account()

        assert report == {"skipped_not_a_demo_tenant": 1}
        card.refresh_from_db()
        assert card.balance.amount == 0
        assert card.transactions.count() == 3
