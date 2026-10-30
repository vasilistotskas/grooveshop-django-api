"""One-click unsubscribe stops EVERY marketing email.

Re-engagement and abandoned-cart mail carry ``List-Unsubscribe`` and
``List-Unsubscribe-Post: List-Unsubscribe=One-Click`` (RFC 8058), but
neither sender looked at any opt-out, and the link only flipped
``UserSubscription`` rows, which these emails do not belong to. A
customer who clicked it, or whose mail client clicked it for them, kept
receiving both.

The customer's refusal now lives on the account
(``UserAccount.marketing_opted_out_at``), recorded by the blanket
unsubscribe link, and each marketing sender skips the account. The tests
drive the real thing: the link taken from the header of an email that was
actually sent.
"""

from __future__ import annotations

import re
from datetime import timedelta

import pytest
from django.core import mail
from django.utils import timezone
from rest_framework import status
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
from user.factories.subscription import UserSubscriptionFactory
from user.models.account import UserAccount
from user.models.subscription import UserSubscription
from user.utils.subscription import (
    generate_blanket_unsubscribe_link,
    generate_unsubscribe_link,
)

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


class TestTheUnsubscribeLinkRecordsTheOptOut:
    def test_one_click_post_opts_the_account_out_without_any_subscription(
        self,
    ):
        """The case that was broken: no topic row to flip."""
        user = UserAccountFactory(num_addresses=0)

        response = _one_click(generate_blanket_unsubscribe_link(user))

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.marketing_opted_out_at is not None

    def test_the_get_link_opts_out_and_says_so(self):
        user = UserAccountFactory(num_addresses=0)
        path = _path(generate_blanket_unsubscribe_link(user))

        first = APIClient().get(path)
        second = APIClient().get(path)

        # "Successfully ..." reports a count; "Already ..." does not.
        assert first.data["count"] == 0
        assert first.data["message"] != second.data["message"]
        assert "count" not in second.data

    def test_the_first_refusal_stands(self):
        user = UserAccountFactory(num_addresses=0)
        path = _path(generate_blanket_unsubscribe_link(user))
        APIClient().get(path)
        user.refresh_from_db()
        first = user.marketing_opted_out_at

        APIClient().get(path)

        user.refresh_from_db()
        assert user.marketing_opted_out_at == first

    def test_a_topic_link_ends_that_topic_only(self):
        subscription = UserSubscriptionFactory(
            status=UserSubscription.SubscriptionStatus.ACTIVE
        )
        user = subscription.user

        response = APIClient().post(
            _path(generate_unsubscribe_link(subscription))
        )

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.marketing_opted_out_at is None

    def test_a_guest_link_opts_no_account_out(self):
        guest = UserSubscriptionFactory(
            user=None,
            email="guest@example.com",
            status=UserSubscription.SubscriptionStatus.ACTIVE,
        )
        bystander = UserAccountFactory(num_addresses=0)
        token = generate_unsubscribe_link(guest).split("/unsubscribe/")[1]
        token = token.split("/")[0]

        APIClient().get(f"/api/v1/user/unsubscribe/{token}")

        bystander.refresh_from_db()
        assert bystander.marketing_opted_out_at is None
        assert not UserAccount.objects.filter(
            marketing_opted_out_at__isnull=False
        ).exists()

    def test_a_forged_link_opts_nobody_out(self):
        user = UserAccountFactory(num_addresses=0)

        response = _one_click(
            generate_blanket_unsubscribe_link(user)[:-4] + "XXXX"
        )

        assert response.status_code == status.HTTP_200_OK
        user.refresh_from_db()
        assert user.marketing_opted_out_at is None


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
