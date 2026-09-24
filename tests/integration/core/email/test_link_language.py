"""Every storefront link in an email opens the storefront in the email's
language.

The storefront runs @nuxtjs/i18n ``prefix_except_default``: ``el`` pages
carry no prefix, ``en`` pages live under ``/en``. So an English email
must link to ``/en/...`` and a Greek one to the bare path — one test per
email family, driven through the real task, with the language taken from
the same row the email picks it from. The prefix rule itself (served vs
not-served locale, empty ``available_locales``, unsupported codes) is
covered in ``tests/unit/core/utils/test_tenant_urls.py``.

No tenant is bound, so every storefront locale is served and links
resolve against ``NUXT_BASE_URL`` (``tenant_storefront_locales(None)``).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from allauth.core.context import request_context
from django.core import mail
from django.test import RequestFactory
from django.utils import timezone
from djmoney.money import Money

from b2b.factories import BusinessProfileFactory
from b2b.tasks import send_business_profile_status_email
from cart.factories.cart import CartFactory
from core.tasks import send_inactive_user_notifications
from giftcard.factories import GiftCardFactory
from giftcard.tasks import deliver_gift_card_email
from loyalty.tasks import notify_loyalty_tier_up_live
from notification.models.notification import Notification
from order.factories.order import OrderFactory
from order.invoicing import generate_invoice
from order.models.stock_reservation import StockReservation
from order.notifications import notify_order_created_live
from order.tasks import (
    send_checkout_abandonment_emails,
    send_invoice_email,
    send_order_status_update_email,
    send_payment_failed_email,
    send_refund_confirmation_email,
)
from product.factories.product import ProductFactory
from product.models.alert import ProductAlert, ProductAlertKind
from product.tasks import (
    send_product_alert_price_drop,
    send_product_alert_restock,
)
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.tasks import acs_send_arrival_notification
from shipping_boxnow.factories import (
    BoxNowParcelEventFactory,
    BoxNowShipmentFactory,
)
from shipping_boxnow.tasks import boxnow_send_arrival_notification
from user.adapter import UserAccountAdapter
from user.factories.account import UserAccountFactory
from user.factories.subscription import UserSubscriptionFactory
from user.models.account import UserAccount
from user.models.data_export import UserDataExport
from user.models.subscription import UserSubscription
from user.tasks import export_user_data_task
from user.utils.subscription import send_subscription_confirmation

BASE = "https://shop.example"

LANGUAGES = pytest.mark.parametrize(
    ("language", "prefix"), [("en", "/en"), ("el", "")]
)


@pytest.fixture(autouse=True)
def _storefront(settings, bind_tenant):
    settings.NUXT_BASE_URL = BASE
    bind_tenant(None)
    mail.outbox = []


def _body(message) -> str:
    """Plain-text body plus every HTML alternative."""
    return message.body + "".join(
        content for content, _mime in getattr(message, "alternatives", [])
    )


def _assert_links(body: str, url: str, language: str) -> None:
    assert url in body
    if language == "el":
        # The bare-host SITE_URL is a prefix of the /en one, so assert
        # the English prefix is absent rather than trusting ``in``.
        assert f"{BASE}/en" not in body


@pytest.mark.django_db
class TestOrderEmails:
    @LANGUAGES
    def test_payment_failed_retry_link(self, language, prefix):
        order = OrderFactory(num_order_items=0, language_code=language)

        assert send_payment_failed_email(order.id) is True

        _assert_links(
            _body(mail.outbox[-1]),
            f"{BASE}{prefix}/account/orders/{order.id}",
            language,
        )

    @LANGUAGES
    def test_status_update_site_link(self, language, prefix):
        order = OrderFactory(num_order_items=0, language_code=language)

        send_order_status_update_email(order.id, "DELIVERED")

        _assert_links(_body(mail.outbox[-1]), f"{BASE}{prefix}", language)


@pytest.mark.django_db
class TestCartRecoveryEmail:
    @LANGUAGES
    def test_recovery_and_preferences_links(self, language, prefix):
        user = UserAccountFactory(num_addresses=0, language_code=language)
        cart = CartFactory(user=user, num_cart_items=0)
        product = ProductFactory(stock=5, num_images=0, num_reviews=0)
        reservation = StockReservation.objects.create(
            product=product,
            quantity=1,
            reserved_by=user,
            session_id=str(cart.uuid),
            expires_at=timezone.now() - timedelta(hours=5),
            consumed=True,
            abandonment_notified=False,
            order=None,
        )
        StockReservation.objects.filter(pk=reservation.pk).update(
            updated_at=timezone.now() - timedelta(hours=5)
        )

        assert send_checkout_abandonment_emails() == 1

        body = _body(mail.outbox[-1])
        _assert_links(
            body, f"{BASE}{prefix}/cart/recover/{cart.uuid}", language
        )
        _assert_links(body, f"{BASE}{prefix}/account/subscriptions/", language)


@pytest.mark.django_db
class TestProductAlertEmail:
    @LANGUAGES
    def test_restock_product_link(self, language, prefix):
        user = UserAccountFactory(num_addresses=0, language_code=language)
        product = ProductFactory(stock=5, num_images=0, num_reviews=0)
        ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK, product=product, user=user
        )

        send_product_alert_restock(product.id)

        _assert_links(
            _body(mail.outbox[-1]),
            f"{BASE}{prefix}/products/{product.id}/{product.slug}",
            language,
        )


@pytest.mark.django_db
class TestGiftCardEmail:
    @LANGUAGES
    def test_delivery_site_link(self, language, prefix):
        issuer = UserAccountFactory(num_addresses=0, language_code=language)
        card = GiftCardFactory(
            issued_to=issuer, recipient_email="friend@example.com"
        )

        deliver_gift_card_email(card.id)

        _assert_links(_body(mail.outbox[-1]), f"{BASE}{prefix}", language)


@pytest.mark.django_db
class TestSubscriptionConfirmationEmail:
    @LANGUAGES
    def test_confirmation_link_follows_the_subscription_language(
        self, language, prefix
    ):
        subscription = UserSubscriptionFactory(
            status=UserSubscription.SubscriptionStatus.PENDING,
            confirmation_token="tok-123",
            language=language,
        )

        assert send_subscription_confirmation(subscription) is True

        _assert_links(
            _body(mail.outbox[-1]),
            f"{BASE}{prefix}/newsletter/confirm/tok-123",
            language,
        )


@pytest.mark.django_db
class TestBusinessProfileEmail:
    @LANGUAGES
    def test_status_email_site_link(self, language, prefix):
        profile = BusinessProfileFactory(
            approved=True,
            user=UserAccountFactory(num_addresses=0, language_code=language),
        )

        send_business_profile_status_email(profile.id)

        _assert_links(_body(mail.outbox[-1]), f"{BASE}{prefix}", language)


@pytest.mark.django_db
class TestShippingEmail:
    @LANGUAGES
    def test_acs_out_for_delivery_order_link(self, language, prefix):
        order = OrderFactory(num_order_items=0, language_code=language)
        shipment = AcsShipmentFactory(order=order, with_voucher=True)

        acs_send_arrival_notification(shipment.id)

        _assert_links(
            _body(mail.outbox[-1]),
            f"{BASE}{prefix}/account/orders/{order.id}",
            language,
        )


def _send_allauth_mail(template_prefix, email, context, *, request_language):
    """Run ``UserAccountAdapter.send_mail`` the way allauth does: inside
    the visitor's request (``AccountMiddleware`` sets
    ``allauth.core.context.request``, which ``BaseAdapter`` reads), and
    return the context the template would render with."""
    captured: dict = {}

    def _render(_template_prefix, _email, rendered_context):
        captured.update(rendered_context)
        return MagicMock(alternatives=[], to=[_email], reply_to=[])

    request = RequestFactory().post(
        "/_allauth/app/v1/auth/password/request",
        HTTP_X_LANGUAGE=request_language,
    )
    with request_context(request):
        adapter = UserAccountAdapter()
        with (
            patch.object(adapter, "render_mail", side_effect=_render),
            patch("tenant.celery.dispatch_on_commit"),
        ):
            adapter.send_mail(template_prefix, email, context)
    return captured


@pytest.mark.django_db
class TestAllauthEmails:
    """allauth resolves these links from ``HEADLESS_FRONTEND_URLS``
    before the recipient is known; ``send_mail`` localizes them. The
    language is the user's when allauth names one, the visitor's
    otherwise."""

    @LANGUAGES
    def test_password_reset_link_follows_the_recipient(self, language, prefix):
        user = UserAccountFactory(num_addresses=0, language_code=language)
        # The visitor browses in the OTHER language: the recipient wins.
        other = "el" if language == "en" else "en"

        captured = _send_allauth_mail(
            "account/email/password_reset_key",
            user.email,
            {
                "user": user,
                "password_reset_url": f"{BASE}/account/password/reset/key/1-a",
            },
            request_language=other,
        )

        assert captured["password_reset_url"] == (
            f"{BASE}{prefix}/account/password/reset/key/1-a"
        )
        assert captured["SITE_URL"] == f"{BASE}{prefix}"

    @LANGUAGES
    def test_unknown_account_link_follows_the_visitor(self, language, prefix):
        captured = _send_allauth_mail(
            "account/email/unknown_account",
            "nobody@example.com",
            {"signup_url": f"{BASE}/account/signup"},
            request_language=language,
        )

        assert captured["signup_url"] == f"{BASE}{prefix}/account/signup"
        assert captured["LANGUAGE_CODE"] == language

    @LANGUAGES
    def test_account_already_exists_links_follow_the_visitor(
        self, language, prefix
    ):
        captured = _send_allauth_mail(
            "account/email/account_already_exists",
            "taken@example.com",
            {
                "signup_url": f"{BASE}/account/signup",
                "password_reset_url": f"{BASE}/account/password/reset",
            },
            request_language=language,
        )

        assert captured["signup_url"] == f"{BASE}{prefix}/account/signup"
        assert captured["password_reset_url"] == (
            f"{BASE}{prefix}/account/password/reset"
        )


@pytest.mark.django_db
class TestInAppNotifications:
    """An in-app notification is read in whatever language its viewer is
    browsing, so its link is a locale-neutral path — whatever the
    recipient's account language — and the storefront localizes it when
    clicked."""

    @pytest.mark.parametrize("language", ["en", "el"])
    def test_order_notification_link_is_neutral(self, language):
        user = UserAccountFactory(num_addresses=0, language_code=language)
        order = OrderFactory(user=user, num_order_items=0)

        notify_order_created_live(order.id)

        assert Notification.objects.filter(
            link=f"/account/orders/{order.id}"
        ).exists()

    @pytest.mark.parametrize("language", ["en", "el"])
    def test_loyalty_notification_link_is_neutral(self, language):
        user = UserAccountFactory(num_addresses=0, language_code=language)

        notify_loyalty_tier_up_live(user.id)

        assert Notification.objects.filter(link="/account/loyalty").exists()

    def test_shipping_notification_link_is_neutral(self):
        user = UserAccountFactory(num_addresses=0, language_code="en")
        order = OrderFactory(user=user, num_order_items=0, language_code="en")
        shipment = AcsShipmentFactory(order=order, with_voucher=True)

        acs_send_arrival_notification(shipment.id)

        assert Notification.objects.filter(
            link=f"/account/orders/{order.id}"
        ).exists()


@pytest.mark.django_db
class TestMoreOrderEmails:
    @LANGUAGES
    def test_refund_site_link(self, language, prefix):
        order = OrderFactory(num_order_items=0, language_code=language)

        assert send_refund_confirmation_email(order.id) is True

        _assert_links(_body(mail.outbox[-1]), f"{BASE}{prefix}", language)

    @LANGUAGES
    def test_invoice_site_link(self, language, prefix):
        order = OrderFactory(num_order_items=0, language_code=language)
        with patch(
            "order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4"
        ):
            generate_invoice(order)
        mail.outbox = []

        assert send_invoice_email(order.id) is True

        _assert_links(_body(mail.outbox[-1]), f"{BASE}{prefix}", language)

    @LANGUAGES
    def test_boxnow_at_locker_order_link(self, language, prefix):
        order = OrderFactory(num_order_items=0, language_code=language)
        event = BoxNowParcelEventFactory(
            shipment=BoxNowShipmentFactory(order=order, with_parcel=True)
        )

        boxnow_send_arrival_notification(event.id)

        _assert_links(
            _body(mail.outbox[-1]),
            f"{BASE}{prefix}/account/orders/{order.id}",
            language,
        )


@pytest.mark.django_db
class TestPriceDropAlertEmail:
    @LANGUAGES
    def test_price_drop_product_link(self, language, prefix):
        user = UserAccountFactory(num_addresses=0, language_code=language)
        product = ProductFactory(stock=5, num_images=0, num_reviews=0)
        ProductAlert.objects.create(
            kind=ProductAlertKind.PRICE_DROP,
            product=product,
            user=user,
            target_price=Money(Decimal("100.00"), "EUR"),
        )

        send_product_alert_price_drop(product.id, 10.0)

        _assert_links(
            _body(mail.outbox[-1]),
            f"{BASE}{prefix}/products/{product.id}/{product.slug}",
            language,
        )


@pytest.mark.django_db
class TestAccountEmails:
    @LANGUAGES
    def test_data_export_download_link(
        self, language, prefix, settings, tmp_path
    ):
        settings.PRIVATE_MEDIA_ROOT = str(tmp_path)
        user = UserAccountFactory(num_addresses=0, language_code=language)
        export = UserDataExport.objects.create(user=user, token="exp-tok")

        export_user_data_task(export.id)

        _assert_links(
            _body(mail.outbox[-1]),
            f"{BASE}{prefix}/account/settings/privacy?export=exp-tok",
            language,
        )

    @LANGUAGES
    def test_reengagement_site_link(self, language, prefix):
        user = UserAccountFactory(num_addresses=0, language_code=language)
        UserAccount.objects.filter(pk=user.pk).update(
            last_login=timezone.now() - timedelta(days=400),
            reengagement_email_count=0,
            last_reengagement_email_at=None,
        )

        send_inactive_user_notifications()

        sent = [m for m in mail.outbox if user.email in m.to]
        assert sent
        _assert_links(_body(sent[-1]), f"{BASE}{prefix}", language)
