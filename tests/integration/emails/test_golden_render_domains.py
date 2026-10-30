"""The product, gift card, B2B, contact, account and cart emails as
customers and the store read them.

Characterisation, like ``test_golden_render``: each case runs a real
Celery task over fixed rows and compares the message that reaches
``mail.outbox`` (subject, recipients, From, Reply-To, headers, HTML and
text, in Greek and English) with the files in ``golden/``. A send site that
moves onto the pipeline must keep them equal; a deliberate change re-records
them in the same commit (``tests/utils/golden.py``).

Every value a body or a header prints is pinned. The signed unsubscribe
tokens carry the clock, so the signer's timestamp is fixed; the two keys
they are signed under are fixed too, so the files do not follow the
developer's ``.env``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from django.core import mail
from django.utils import translation
from django.utils.translation import gettext
from django_celery_beat.models import IntervalSchedule, PeriodicTask
from djmoney.money import Money

from b2b.enum import BusinessProfileStatus, ViesStatus
from b2b.factories import BusinessProfileFactory, CustomerGroupFactory
from b2b.tasks import (
    send_admin_new_business_profile_email,
    send_business_profile_status_email,
)
from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from contact.factories import ContactFactory, FeedbackFactory
from contact.models import FeedbackCategory
from contact.tasks import (
    send_contact_notification_email_task,
    send_feedback_notification_email_task,
)
from core.tasks import send_inactive_user_notifications
from giftcard.enum import GiftCardPurchaseStatus, GiftCardStatus
from giftcard.factories import GiftCardFactory, GiftCardPurchaseFactory
from giftcard.models import GiftCard
from giftcard.tasks import (
    deliver_gift_card_email,
    send_gift_card_expiry_reminders,
    send_gift_card_purchase_receipt,
)
from order.models.stock_reservation import StockReservation
from order.tasks import send_checkout_abandonment_emails
from product.factories.product import ProductFactory
from product.models.alert import ProductAlert, ProductAlertKind
from product.tasks import (
    check_low_stock_products,
    send_product_alert_price_drop,
    send_product_alert_restock,
)
from tests.utils.golden import assert_golden, snapshot
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)
from tests.utils.store_settings import switch_store_settings
from user.factories.account import UserAccountFactory
from user.factories.subscription import SubscriptionTopicFactory
from user.models.account import UserAccount
from user.models.data_export import UserDataExport
from user.models.subscription import SubscriptionTopic, UserSubscription
from user.tasks import export_user_data_task
from user.utils.subscription import send_subscription_confirmation

pytestmark = pytest.mark.django_db

GOLDEN = Path(__file__).parent / "golden"

STORE_INBOX = "owner@store.test"
CUSTOMER_INBOX = "customer@example.test"
NOW = datetime(2026, 3, 14, 10, 30, tzinfo=UTC)
CART_UUID = UUID("00000000-0000-4000-8000-0000000000c1")
TOKEN_STAMP = "1AbCdE"
BEAT_ENTRY = "send-inactive-user-notifications"


@pytest.fixture(params=["el", "en"])
def language(request):
    return request.param


@pytest.fixture(autouse=True)
def _greek_catalogue_is_compiled():
    with translation.override("el"):
        assert gettext("We miss you!") != "We miss you!", (
            "run `uv run python manage.py compilemessages --ignore=.venv`"
        )


@pytest.fixture(autouse=True)
def _store(settings, language):
    """A bound store in the language under test, fixed platform sender,
    storefront, API host and signing keys, every feature open, the
    re-engagement beat entry enabled, and a clock the signed tokens read."""
    settings.NUXT_BASE_URL = "https://shop.example"
    settings.API_BASE_URL = "https://api.shop.example"
    settings.DEFAULT_FROM_EMAIL = "noreply@platform.test"
    settings.SECRET_KEY = "golden-secret-key-not-used-anywhere-else"
    settings.EMAIL_HASH_KEY = "golden-hash-key-not-used-anywhere-else"
    switch_store_settings(PRODUCT_ALERTS_ENABLED=True)
    PeriodicTask.objects.update_or_create(
        name=BEAT_ENTRY,
        defaults={
            "task": "tenant.tasks.fanout_send_inactive_user_notifications",
            "enabled": True,
            "interval": IntervalSchedule.objects.create(
                every=1, period=IntervalSchedule.DAYS
            ),
        },
    )
    previous = bind_store_tenant(
        store_tenant(
            "acme",
            default_locale=language,
            store_name="Acme Gadgets",
            contact_email=STORE_INBOX,
            owner_email=STORE_INBOX,
        )
    )
    with (
        patch("emails.gates.feature_on", return_value=True),
        patch(
            "giftcard.services.GiftCardService.is_enabled", return_value=True
        ),
        patch(
            "django.core.signing.TimestampSigner.timestamp",
            return_value=TOKEN_STAMP,
        ),
    ):
        yield
    unbind_store_tenant(previous)
    mail.outbox.clear()


def customer(language: str, **fields):
    return UserAccountFactory(
        num_addresses=0,
        email=CUSTOMER_INBOX,
        first_name="Maria",
        last_name="Papadopoulou",
        username="maria",
        id=500_001,
        language_code=language,
        **fields,
    )


def named_product(**fields):
    """A product named in both languages, so the language of the mail is
    visible in what it prints."""
    product = ProductFactory(num_images=0, num_reviews=0, **fields)
    for code, name in (
        ("el", "Ασύρματα ακουστικά"),
        ("en", "Wireless earbuds"),
    ):
        product.set_current_language(code)
        product.name = name
        product.save()
    return product


def assert_sent(name: str, language: str, *, to: str | None = None) -> None:
    sent = [m for m in mail.outbox if to is None or to in m.to]
    [message] = sent
    assert_golden(GOLDEN, name, language, snapshot(message))


class TestProductEmails:
    def test_restock_alert_for_a_member(self, language):
        product = named_product(id=700_001, slug="wireless-earbuds", stock=5)
        ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK,
            product=product,
            user=customer(language),
        )
        mail.outbox.clear()

        send_product_alert_restock.run(product.pk)

        assert_sent("product_restock_alert_member", language)

    def test_restock_alert_for_a_guest(self, language):
        product = named_product(id=700_001, slug="wireless-earbuds", stock=5)
        ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK,
            product=product,
            email="guest@example.test",
            language_code=language,
        )
        mail.outbox.clear()

        send_product_alert_restock.run(product.pk)

        assert_sent("product_restock_alert_guest", language)

    def test_price_drop_alert_for_a_member(self, language):
        product = named_product(id=700_001, slug="wireless-earbuds", stock=5)
        ProductAlert.objects.create(
            kind=ProductAlertKind.PRICE_DROP,
            product=product,
            user=customer(language),
            target_price=Money(Decimal("30.00"), "EUR"),
        )
        mail.outbox.clear()

        send_product_alert_price_drop.run(product.pk, 25.5)

        assert_sent("product_price_drop_alert_member", language)

    def test_low_stock_alert(self, language):
        named_product(
            id=700_001,
            slug="wireless-earbuds",
            sku="SKU-GOLD-1",
            stock=2,
            low_stock_threshold=5,
            low_stock_alert_sent=False,
            active=True,
        )
        mail.outbox.clear()

        assert check_low_stock_products.run() == {
            "alerted": 1,
            "ids": [700_001],
        }

        assert_sent("team_low_stock", language)


class TestGiftCardEmails:
    def _card(self, language, **fields):
        return GiftCardFactory(
            code="GC-GOLD-0001",
            initial_value=Money(Decimal("50.00"), "EUR"),
            recipient_email="recipient@example.test",
            recipient_name="Giorgos",
            sender_name="Maria",
            message="Happy birthday!",
            expires_at=datetime(2030, 1, 31, 12, 0, tzinfo=UTC),
            issued_to=customer(language),
            **fields,
        )

    def test_delivery(self, language):
        card = self._card(language)
        mail.outbox.clear()

        assert deliver_gift_card_email.run(card.pk)["status"] == "success"

        assert_sent("giftcard_delivery", language)

    def test_purchase_receipt(self, language):
        purchase = GiftCardPurchaseFactory(
            buyer=customer(language),
            buyer_email=CUSTOMER_INBOX,
            recipient_email="recipient@example.test",
            recipient_name="Giorgos",
            sender_name="Maria",
            message="Happy birthday!",
            amount=Money(Decimal("50.00"), "EUR"),
            status=GiftCardPurchaseStatus.PAID,
            deliver_at=datetime(2026, 4, 1, 9, 0, tzinfo=UTC),
        )
        mail.outbox.clear()

        result = send_gift_card_purchase_receipt.run(purchase.pk)

        assert result["status"] == "success"
        assert_sent("giftcard_purchase_receipt", language)

    def test_expiry_reminder(self, language):
        self._card(
            language,
            status=GiftCardStatus.ACTIVE,
            expiry_reminder_sent_at=None,
        )
        mail.outbox.clear()

        GiftCard.objects.update(expires_at=NOW + timedelta(days=5))
        with patch("django.utils.timezone.now", return_value=NOW):
            result = send_gift_card_expiry_reminders.run()

        assert result == {"status": "success", "sent": 1}
        assert_sent("giftcard_expiry_reminder", language)


class TestB2BEmails:
    def test_profile_approved(self, language):
        profile = BusinessProfileFactory(
            user=customer(language),
            status=BusinessProfileStatus.APPROVED,
            company_name="Acme Trading IKE",
            vat_id="094019245",
            customer_group=CustomerGroupFactory(name="Wholesale Gold"),
        )
        mail.outbox.clear()

        result = send_business_profile_status_email.run(profile.pk)

        assert result["status"] == "success"
        assert_sent("b2b_profile_approved", language)

    def test_profile_rejected(self, language):
        profile = BusinessProfileFactory(
            user=customer(language),
            status=BusinessProfileStatus.REJECTED,
            company_name="Acme Trading IKE",
            vat_id="094019245",
        )
        mail.outbox.clear()

        result = send_business_profile_status_email.run(profile.pk)

        assert result["status"] == "success"
        assert_sent("b2b_profile_rejected", language)

    def test_application_alert_for_the_store(self, language):
        profile = BusinessProfileFactory(
            user=customer(language),
            status=BusinessProfileStatus.PENDING,
            company_name="Acme Trading IKE",
            vat_id="094019245",
            tax_office="Α' Αθηνών",
            activity="Retail trade",
            vies_status=ViesStatus.UNCHECKED,
        )
        mail.outbox.clear()

        result = send_admin_new_business_profile_email.run(profile.pk)

        assert result["status"] == "success"
        assert_sent("team_b2b_application", language)


class TestContactEmails:
    def test_contact_message(self, language):
        contact = ContactFactory(
            name="Nikos Georgiou",
            email=CUSTOMER_INBOX,
            message="Do you ship to Crete?",
            company="Georgiou SA",
            phone="+302101234567",
            subject="Shipping to Crete",
        )
        mail.outbox.clear()

        assert send_contact_notification_email_task.run(contact.pk) is True

        assert_sent("team_contact_message", language)

    def test_feedback(self, language):
        feedback = FeedbackFactory(
            name="Nikos Georgiou",
            email=CUSTOMER_INBOX,
            rating=4,
            category=FeedbackCategory.GENERAL,
            message="Quick checkout, thank you.",
        )
        mail.outbox.clear()

        assert send_feedback_notification_email_task.run(feedback.pk) is True

        assert_sent("team_feedback", language)


class TestAccountEmails:
    def test_data_export_ready(self, language, settings, tmp_path):
        settings.PRIVATE_MEDIA_ROOT = str(tmp_path)
        user = customer(language)
        export = UserDataExport.objects.create(user=user, token="export-token")
        mail.outbox.clear()

        # The archive's size is printed; a fixed payload fixes it.
        with (
            patch("django.utils.timezone.now", return_value=NOW),
            patch(
                "user.services.gdpr.compile_user_data",
                return_value={"user": {"id": user.pk}},
            ),
        ):
            result = export_user_data_task.run(export.pk)

        assert result["status"] == "success"
        assert_sent("user_data_export_ready", language)

    def test_newsletter_confirmation(self, language):
        topic = SubscriptionTopicFactory(
            slug="weekly-news",
            category=SubscriptionTopic.TopicCategory.MARKETING,
        )
        for code, name in (("el", "Εβδομαδιαία νέα"), ("en", "Weekly news")):
            topic.set_current_language(code)
            topic.name = name
            topic.description = f"{name} digest"
            topic.save()
        subscription = UserSubscription.objects.create(
            user=customer(language),
            topic=topic,
            status=UserSubscription.SubscriptionStatus.PENDING,
            language=language,
            confirmation_token="confirm-token",
        )
        UserSubscription.objects.filter(pk=subscription.pk).update(
            subscribed_at=NOW
        )
        subscription.refresh_from_db()
        mail.outbox.clear()

        assert send_subscription_confirmation(subscription) is True

        assert_sent("newsletter_confirmation", language)


class TestMarketingEmails:
    def test_reengagement(self, language):
        user = customer(language)
        UserAccount.objects.filter(pk=user.pk).update(
            last_login=NOW - timedelta(days=400),
            reengagement_email_count=0,
            last_reengagement_email_at=None,
        )
        mail.outbox.clear()

        send_inactive_user_notifications.run()

        assert_sent("user_reengagement", language, to=CUSTOMER_INBOX)

    def test_checkout_abandoned(self, language):
        user = customer(language)
        cart = CartFactory(user=user, num_cart_items=0, uuid=CART_UUID)
        product = named_product(
            id=700_001,
            slug="wireless-earbuds",
            stock=5,
            price=Money(Decimal("20.00"), "EUR"),
        )
        CartItemFactory(cart=cart, product=product, quantity=2)
        reservation = StockReservation.objects.create(
            product=product,
            quantity=2,
            reserved_by=user,
            session_id=str(cart.uuid),
            expires_at=timezone_ago(hours=5),
            consumed=True,
            abandonment_notified=False,
            order=None,
        )
        StockReservation.objects.filter(pk=reservation.pk).update(
            updated_at=timezone_ago(hours=5)
        )
        mail.outbox.clear()

        assert send_checkout_abandonment_emails.run() == 1

        assert_sent("cart_checkout_abandoned", language)


def timezone_ago(*, hours: int) -> datetime:
    from django.utils import timezone

    return timezone.now() - timedelta(hours=hours)
