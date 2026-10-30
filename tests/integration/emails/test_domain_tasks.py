"""What the migrated tasks do with a skip.

``send_email`` answers a send with a skip when the type's gate is closed,
the store switched it off, nobody is left to write to, or the recipient
opted out. A task decides what that means for its own claim: an alert or a
reminder that did not go out because the type is OFF must stay armed for
when it is back on; one that is settled for good (an opt-out, a reminder
the store no longer wants) must not be found again tomorrow.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core import mail
from django.utils import timezone
from djmoney.money import Money

from cart.factories.cart import CartFactory
from contact.factories import ContactFactory
from contact.tasks import send_contact_notification_email_task
from emails import optout
from emails.gates import FeatureGate
from emails.models import EmailOptOut
from emails.services import set_switch
from giftcard.enum import GiftCardStatus
from giftcard.factories import GiftCardFactory
from giftcard.tasks import (
    deliver_gift_card_email,
    send_gift_card_expiry_reminders,
)
from order.models.stock_reservation import StockReservation
from order.tasks import send_checkout_abandonment_emails
from product.factories.product import ProductFactory
from product.models.alert import ProductAlert, ProductAlertKind
from product.tasks import check_low_stock_products, send_product_alert_restock
from tests.utils.staff import store_staff
from user.factories.account import UserAccountFactory

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("tenant", "open_email_gates"),
]

OPERATOR = "owner@store.test"


@pytest.fixture(autouse=True)
def _operators():
    mail.outbox.clear()
    with patch(
        "emails.recipients.tenant_operator_addresses", return_value=[OPERATOR]
    ):
        yield


def switch_off(tenant, key: str) -> None:
    set_switch(store_staff(tenant, is_superuser=True), key, enabled=False)


def closed(feature: str):
    """The gate of *feature* closed, whatever else is open."""
    original = FeatureGate.is_open
    return patch.object(
        FeatureGate,
        "is_open",
        lambda gate: False if gate.feature == feature else original(gate),
    )


class TestProductAlerts:
    @pytest.fixture
    def alert(self):
        product = ProductFactory(stock=5, num_images=0, num_reviews=0)
        return ProductAlert.objects.create(
            kind=ProductAlertKind.RESTOCK,
            product=product,
            email="guest@example.test",
        )

    def test_a_closed_feature_leaves_the_alert_armed(self, alert):
        with closed("product_alerts"):
            result = send_product_alert_restock.run(alert.product_id)

        assert result["status"] == "skipped"
        assert mail.outbox == []
        alert.refresh_from_db()
        assert alert.is_active is True
        assert alert.notified_at is None

    def test_a_switched_off_type_leaves_the_alert_armed(self, alert, tenant):
        switch_off(tenant, "product.restock_alert")

        result = send_product_alert_restock.run(alert.product_id)

        assert result["status"] == "skipped"
        alert.refresh_from_db()
        assert alert.is_active is True

    def test_an_opt_out_settles_the_alert_without_a_mail(self, alert):
        optout.opt_out(
            alert.email,
            "product.restock_alert",
            source=EmailOptOut.Source.ONE_CLICK,
        )

        result = send_product_alert_restock.run(alert.product_id)

        assert result == {
            "status": "success",
            "product_id": alert.product_id,
            "sent": 0,
        }
        assert mail.outbox == []
        alert.refresh_from_db()
        assert alert.is_active is False
        assert alert.notified_at is not None


class TestLowStock:
    @pytest.fixture
    def product(self):
        return ProductFactory(
            active=True,
            num_images=0,
            num_reviews=0,
            stock=1,
            low_stock_threshold=5,
            low_stock_alert_sent=False,
        )

    def test_a_store_with_nobody_to_write_to_releases_the_claim(self, product):
        with patch(
            "emails.recipients.tenant_operator_addresses", return_value=[]
        ):
            result = check_low_stock_products.run()

        assert result["alerted"] == 0
        product.refresh_from_db()
        assert product.low_stock_alert_sent is False

    def test_a_switched_off_alert_releases_the_claim(self, product, tenant):
        switch_off(tenant, "team.low_stock")

        result = check_low_stock_products.run()

        assert result["alerted"] == 0
        assert mail.outbox == []
        product.refresh_from_db()
        assert product.low_stock_alert_sent is False

    def test_a_sent_alert_keeps_the_claim(self, product):
        result = check_low_stock_products.run()

        assert result["alerted"] == 1
        product.refresh_from_db()
        assert product.low_stock_alert_sent is True


class TestGiftCards:
    @pytest.fixture
    def card(self):
        return GiftCardFactory(
            code="GC-DOMAIN-0001",
            initial_value=Money(Decimal("50.00"), "EUR"),
            recipient_email="recipient@example.test",
            status=GiftCardStatus.ACTIVE,
            issued_to=UserAccountFactory(num_addresses=0),
        )

    def test_delivery_marks_the_card_delivered(self, card):
        assert deliver_gift_card_email.run(card.pk)["status"] == "success"

        card.refresh_from_db()
        assert card.delivered_at is not None

    def test_a_closed_feature_leaves_the_card_undelivered(self, card):
        with closed("gift_cards"):
            result = deliver_gift_card_email.run(card.pk)

        assert result["status"] == "skipped"
        assert mail.outbox == []
        card.refresh_from_db()
        assert card.delivered_at is None

    def test_a_switched_off_reminder_stays_unsent(self, card, tenant):
        soon = timezone.now() + timedelta(days=2)
        type(card).objects.filter(pk=card.pk).update(expires_at=soon)
        switch_off(tenant, "giftcard.expiry_reminder")

        with patch(
            "giftcard.services.GiftCardService.is_enabled", return_value=True
        ):
            result = send_gift_card_expiry_reminders.run()

        assert result["status"] == "skipped"
        assert mail.outbox == []
        card.refresh_from_db()
        assert card.expiry_reminder_sent_at is None

    def test_a_reminder_is_recorded_once_sent(self, card):
        soon = datetime.now(UTC) + timedelta(days=2)
        type(card).objects.filter(pk=card.pk).update(expires_at=soon)

        with patch(
            "giftcard.services.GiftCardService.is_enabled", return_value=True
        ):
            result = send_gift_card_expiry_reminders.run()

        assert result == {"status": "success", "sent": 1}
        card.refresh_from_db()
        assert card.expiry_reminder_sent_at is not None


class TestAbandonedCheckout:
    def test_a_switched_off_reminder_settles_the_reservation(self, tenant):
        user = UserAccountFactory(num_addresses=0)
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
        switch_off(tenant, "cart.checkout_abandoned")
        mail.outbox.clear()

        assert send_checkout_abandonment_emails.run() == 0

        assert mail.outbox == []
        reservation.refresh_from_db()
        assert reservation.abandonment_notified is True


class TestContact:
    def test_a_switched_off_alert_says_nothing_was_sent(self, tenant):
        contact = ContactFactory()
        switch_off(tenant, "team.contact_message")
        mail.outbox.clear()

        assert send_contact_notification_email_task.run(contact.pk) is False

        assert mail.outbox == []
