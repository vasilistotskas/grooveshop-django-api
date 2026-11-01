"""One-click unsubscribe stops EVERY marketing email.

Re-engagement and abandoned-cart mail carry ``List-Unsubscribe`` and
``List-Unsubscribe-Post: List-Unsubscribe=One-Click`` (RFC 8058), but
neither sender looked at any opt-out, and the link only flipped
``UserSubscription`` rows, which these emails do not belong to. A
customer who clicked it, or whose mail client clicked it for them, kept
receiving both.

The customer's refusal lives in ``EmailOptOut`` and each marketing send
skips it. The tests drive the real thing: the link taken from the header of
an email that was actually sent.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework.test import APIClient

from cart.factories.cart import CartFactory
from core.tasks import send_inactive_user_notifications
from emails import optout
from emails.models import EVERY_MARKETING_TYPE, EmailOptOut
from order.models.stock_reservation import StockReservation
from order.tasks import send_checkout_abandonment_emails
from product.factories.product import ProductFactory
from tests.utils.emails import open_email_gates  # noqa: F401
from user.factories.account import UserAccountFactory
from user.models.account import UserAccount

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("open_email_gates"),
]

API = "https://api.test-site.com"


@pytest.fixture(autouse=True)
def _urls(settings):
    settings.API_BASE_URL = API
    settings.NUXT_BASE_URL = "https://shop.example"
    settings.DEFAULT_FROM_EMAIL = "no-reply@test.com"
    mail.outbox = []


def _path(url: str) -> str:
    return url.removeprefix(API)


def _one_click_url(message) -> str:
    """The HTTPS form of the ``List-Unsubscribe`` header, as a mail
    client reads it."""
    header = message.extra_headers["List-Unsubscribe"]
    assert message.extra_headers["List-Unsubscribe-Post"] == (
        "List-Unsubscribe=One-Click"
    )
    return re.search(r"<(https://[^>]+)>", header).group(1)


def _one_click(url: str):
    """What Gmail does: a POST with the RFC 8058 body, no cookies."""
    return APIClient().post(
        _path(url),
        data="List-Unsubscribe=One-Click",
        content_type="application/x-www-form-urlencoded",
    )


def _inactive_user(**kwargs):
    return UserAccountFactory(
        num_addresses=0,
        language_code="en",
        last_login=timezone.now() - timedelta(days=400),
        reengagement_email_count=0,
        last_reengagement_email_at=None,
        **kwargs,
    )


def _abandoned_cart(user) -> StockReservation:
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
    return reservation


class TestReengagementHonoursTheOptOut:
    def test_the_one_click_link_in_the_email_stops_the_next_one(self):
        leaver = _inactive_user(email="leaver@example.com")
        stayer = _inactive_user(email="stayer@example.com")

        send_inactive_user_notifications()
        assert sorted(m.to[0] for m in mail.outbox) == [
            "leaver@example.com",
            "stayer@example.com",
        ]
        leaver_mail = next(m for m in mail.outbox if m.to == [leaver.email])
        UserAccount.objects.update(
            reengagement_email_count=0, last_reengagement_email_at=None
        )
        mail.outbox = []

        assert _one_click(_one_click_url(leaver_mail)).status_code == 200
        send_inactive_user_notifications()

        assert [m.to[0] for m in mail.outbox] == [stayer.email]

    def test_the_body_link_opens_the_confirm_page_and_opts_nobody_out(self):
        user = _inactive_user(email="leaver@example.com")

        send_inactive_user_notifications()

        message = mail.outbox[0]
        match = re.search(
            r"https://\S+/email/unsubscribe/([^\s\"<>]+)", message.body
        )
        assert match is not None
        claims = optout.read_token(match.group(1))
        assert claims.address_hash == optout.address_hash(user.email)
        assert claims.type_key == "user.reengagement"
        assert "/api/v1/" not in match.group(0)
        assert not EmailOptOut.objects.exists()

    def test_an_opted_out_account_is_not_mailed_or_counted_as_sent(self):
        user = _inactive_user()
        optout.opt_out(
            user.email, EVERY_MARKETING_TYPE, source=EmailOptOut.Source.LINK
        )

        result = send_inactive_user_notifications()

        assert result["emails_sent"] == 0
        assert mail.outbox == []


class TestAbandonedCartHonoursTheOptOut:
    def test_the_one_click_link_in_the_email_stops_the_next_one(self):
        leaver = UserAccountFactory(num_addresses=0, language_code="en")
        stayer = UserAccountFactory(num_addresses=0, language_code="en")
        leaver_reservation = _abandoned_cart(leaver)
        stayer_reservation = _abandoned_cart(stayer)

        assert send_checkout_abandonment_emails() == 2
        leaver_mail = next(m for m in mail.outbox if m.to == [leaver.email])
        StockReservation.objects.update(abandonment_notified=False)
        mail.outbox = []

        assert _one_click(_one_click_url(leaver_mail)).status_code == 200
        assert send_checkout_abandonment_emails() == 1

        assert [m.to[0] for m in mail.outbox] == [stayer.email]
        # The opted-out cart is settled, so tomorrow's run does not find
        # it again, and lifting the opt-out later does not send it late.
        leaver_reservation.refresh_from_db()
        assert leaver_reservation.abandonment_notified is True
        stayer_reservation.refresh_from_db()
        assert stayer_reservation.abandonment_notified is True

    def test_an_opted_out_account_gets_no_reminder(self):
        user = UserAccountFactory(num_addresses=0)
        optout.opt_out(
            user.email, EVERY_MARKETING_TYPE, source=EmailOptOut.Source.LINK
        )
        _abandoned_cart(user)

        assert send_checkout_abandonment_emails() == 0
        assert mail.outbox == []
