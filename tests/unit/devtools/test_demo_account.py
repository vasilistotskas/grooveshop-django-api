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
        from pay_way.enum.settlement import PaySettlement
        from pay_way.factories import PayWayFactory
        from product.factories.product import ProductFactory

        for slug in {
            slug for row in demo_account.ORDERS for slug, _q in row.items
        }:
            ProductFactory(slug=slug, stock=10, num_images=0, num_reviews=0)
        PayWayFactory.create_online_payment()
        PayWayFactory(settlement=PaySettlement.CARRIER_TERMINAL.value)

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
            if row.payment_status == "COMPLETED":
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
