"""The operational alerts as the store's staff read them.

Like ``test_golden_render_domains``: each case runs the real code that sends
the alert over fixed rows and compares the message that reaches
``mail.outbox`` (subject, recipients, From, headers, HTML and text, in Greek
and English) with the files in ``golden/``. These mails had no template and
no translation before they became email types, so the files record what they
are now; a deliberate change re-records them in the same commit
(``tests/utils/golden.py``).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest
from django.core import mail
from django.utils import translation
from django.utils.translation import gettext
from djmoney.money import Money

from emails.send import send_email
from giftcard.emails import GiftCardReversal
from giftcard.enum import GiftCardPurchaseStatus
from giftcard.factories import GiftCardPurchaseFactory
from giftcard.services import GiftCardService
from order.emails import RefundFailed
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.services import OrderService
from product.enum.review import ReviewStatus
from product.factories.product import ProductFactory
from product.factories.review import ProductReviewFactory
from product.tasks import send_review_pending_alert
from shipping.alerts import (
    alert_admins_shipment_creation_failed,
    alert_admins_webhook_processing_failed,
)
from shipping_acs.enum.charge_type import AcsChargeType
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.services import AcsService
from shipping_acs.tasks import (
    alert_unremitted_cod_payouts,
    check_stale_acs_shipments,
)
from tests.utils.golden import assert_golden, snapshot
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

GOLDEN = Path(__file__).parent / "golden"

STORE_INBOX = "owner@store.test"
NOW = datetime(2026, 3, 14, 10, 30, tzinfo=UTC)
PURCHASE_UUID = UUID("00000000-0000-4000-8000-0000000000e1")


@pytest.fixture(params=["el", "en"])
def language(request):
    return request.param


@pytest.fixture(autouse=True)
def _greek_catalogue_is_compiled():
    with translation.override("el"):
        assert gettext("Stale shipments digest") != "Stale shipments digest", (
            "run `uv run python manage.py compilemessages --ignore=.venv`"
        )


@pytest.fixture(autouse=True)
def _store(settings, language, open_email_gates):
    """A bound store in the language under test, fixed platform sender and
    hosts, every gate open, and a clock the parcel ages read."""
    settings.NUXT_BASE_URL = "https://shop.example"
    settings.API_BASE_URL = "https://api.shop.example"
    settings.DEFAULT_FROM_EMAIL = "noreply@platform.test"
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
        patch("shipping_acs.config.is_configured", return_value=True),
        patch("django.utils.timezone.now", return_value=NOW),
    ):
        yield
    unbind_store_tenant(previous)
    mail.outbox.clear()


def assert_sent(name: str, language: str) -> None:
    [message] = mail.outbox
    assert_golden(GOLDEN, name, language, snapshot(message))


class TestCarrierAlerts:
    def test_shipment_rejected(self, language):
        alert_admins_shipment_creation_failed(
            order_id=1234,
            carrier="ACS",
            error="ACS [ACS_Create_Voucher]: fill data error",
            remedy=(
                "Fix the address on the order, then press “Issue ACS "
                "voucher now” on the order's ACS shipment."
            ),
        )

        assert_sent("ops_shipment_rejected", language)

    def test_webhook_failed(self, language):
        alert_admins_webhook_processing_failed(
            carrier="BoxNow",
            message_id="3f1c2a90-5b7e-4c1d-9d1e-0a6c1f2b7e11",
            error="KeyError: 'parcelId'",
        )

        assert_sent("ops_webhook_failed", language)


class TestAcsAlerts:
    def test_stale_shipments(self, language):
        AcsShipmentFactory(
            voucher_no="9700000001",
            order=OrderFactory(id=1201, num_order_items=0),
            shipment_state=AcsShipmentState.NEW,
            last_event_at=NOW - timedelta(days=5),
        )
        AcsShipmentFactory(
            voucher_no="9700000002",
            order=OrderFactory(id=1188, num_order_items=0),
            shipment_state=AcsShipmentState.IN_TRANSIT,
            last_event_at=NOW - timedelta(days=9),
        )
        mail.outbox.clear()

        assert check_stale_acs_shipments.run()["alerted"] == 2

        assert_sent("ops_stale_shipments", language)

    def test_unremitted_cod(self, language):
        for order_id, voucher, amount, days in (
            (1201, "9772892603", "24.48", 30),
            (1188, "9772892710", "61.00", 28),
        ):
            AcsShipmentFactory(
                order=OrderFactory(
                    id=order_id,
                    num_order_items=0,
                    status=OrderStatus.DELIVERED,
                    payment_status=PaymentStatus.PENDING,
                ),
                voucher_no=voucher,
                shipment_state=AcsShipmentState.DELIVERED,
                charge_type=AcsChargeType.COD,
                cod_amount=Money(Decimal(amount), "EUR"),
                delivery_date=NOW - timedelta(days=days),
            )
        mail.outbox.clear()

        assert alert_unremitted_cod_payouts.run()["alerted"] == 2

        assert_sent("ops_unremitted_cod", language)

    def test_unattributable_cod_payouts(self, language):
        AcsService._alert_admins_unmatched_payouts(
            unmatched=2, skipped=1, cod_payment_date=date(2026, 3, 13)
        )

        assert_sent("ops_unattributable_cod_payouts", language)


class TestPaymentAlerts:
    def _canceled_order(self):
        return OrderFactory(
            id=1234,
            num_order_items=0,
            status=OrderStatus.CANCELED,
            payment_status=PaymentStatus.CANCELED,
        )

    def test_payment_after_cancel(self, language):
        order = self._canceled_order()
        mail.outbox.clear()

        OrderService.record_payment_after_cancel(
            order, payment_id="pi_3QxExample", payment_method="stripe"
        )

        assert_sent("ops_payment_after_cancel", language)

    def test_payment_mismatch(self, language):
        order = OrderFactory(
            id=1234,
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        mail.outbox.clear()

        OrderService.flag_payment_mismatch(
            order,
            payment_id="pi_3QxExample",
            provider="Stripe",
            observed=Money(Decimal("40.00"), "EUR"),
            expected=Money(Decimal("45.50"), "EUR"),
        )

        assert_sent("ops_payment_mismatch", language)

    def test_refund_failed(self, language):
        # The handler that raises it (``handle_stripe_refund_failed``) needs
        # a whole Stripe refund to run; its own tests cover that path.
        send_email(
            "ops.refund_failed",
            RefundFailed(
                order_id=1234,
                provider="Stripe",
                refund_id="re_3QxExample",
                amount=Money(Decimal("45.50"), "EUR"),
                reason="expired_or_canceled_card",
            ),
        )

        assert_sent("ops_refund_failed", language)


class TestGiftCardAlerts:
    @pytest.fixture
    def purchase(self):
        return GiftCardPurchaseFactory(
            uuid=PURCHASE_UUID,
            buyer=UserAccountFactory(num_addresses=0),
            amount=Money(Decimal("50.00"), "EUR"),
            status=GiftCardPurchaseStatus.PAID,
        )

    def test_reversed_after_part_was_spent(self, language, purchase):
        mail.outbox.clear()

        send_email(
            "ops.gift_card_reversal",
            GiftCardReversal(purchase, unrecovered=Decimal("20.00")),
        )

        assert_sent("ops_gift_card_reversed", language)

    def test_disputed(self, language, purchase):
        mail.outbox.clear()

        assert GiftCardService.alert_purchase_disputed(purchase, "dp_1")

        assert_sent("ops_gift_card_disputed", language)


class TestReviewAlert:
    def test_review_pending(self, language):
        product = ProductFactory(id=700_001, num_images=0, num_reviews=0)
        for code, name in (
            ("el", "Ασύρματα ακουστικά"),
            ("en", "Wireless earbuds"),
        ):
            product.set_current_language(code)
            product.name = name
            product.save()
        review = ProductReviewFactory(
            id=900_001,
            product=product,
            user=UserAccountFactory(
                num_addresses=0,
                first_name="Maria",
                last_name="Papadopoulou",
                username="maria",
            ),
            rate=8,
            status=ReviewStatus.NEW,
            translations=[],
        )
        for code in ("el", "en"):
            review.set_current_language(code)
            review.comment = "Great sound, the battery lasts all week."
        review.save()
        mail.outbox.clear()

        assert send_review_pending_alert.run(review.pk)["status"] == "sent"

        assert_sent("ops_review_pending", language)
