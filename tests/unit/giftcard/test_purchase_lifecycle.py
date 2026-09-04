"""Purchase reversal handling, expiry reminders, and Viva branch."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from djmoney.money import Money

from giftcard.enum import (
    GiftCardPurchaseStatus,
    GiftCardStatus,
    GiftCardTransactionKind,
)
from giftcard.factories import GiftCardFactory, GiftCardPurchaseFactory
from giftcard.models import GiftCardTransaction
from giftcard.services import GiftCardService

pytestmark = pytest.mark.django_db


class TestPurchaseReversal:
    def test_pending_purchase_is_cancelled(self, enable_gift_cards):
        purchase = GiftCardPurchaseFactory()

        outcome = GiftCardService.handle_purchase_reversal(purchase)

        purchase.refresh_from_db()
        assert outcome == "processed"
        assert purchase.status == GiftCardPurchaseStatus.CANCELED

    def test_untouched_card_is_voided(self, enable_gift_cards):
        purchase = GiftCardPurchaseFactory(amount=Money(Decimal(50), "EUR"))
        card = GiftCardService.complete_purchase(purchase, "txn_1")

        outcome = GiftCardService.handle_purchase_reversal(purchase)

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert outcome == "processed"
        assert purchase.status == GiftCardPurchaseStatus.CANCELED
        assert card.status == GiftCardStatus.DISABLED
        assert card.balance.amount == Decimal(0)

    def test_spent_card_is_left_for_ops(self, enable_gift_cards):
        purchase = GiftCardPurchaseFactory(amount=Money(Decimal(50), "EUR"))
        card = GiftCardService.complete_purchase(purchase, "txn_2")
        GiftCardTransaction.objects.create(
            gift_card=card,
            kind=GiftCardTransactionKind.REDEEM,
            amount=Decimal(-10),
        )

        GiftCardService.handle_purchase_reversal(purchase)

        card.refresh_from_db()
        # Partially spent — never auto-voided.
        assert card.status == GiftCardStatus.ACTIVE
        assert card.balance.amount == Decimal(40)


class TestExpiryReminders:
    def test_reminder_sent_once_within_window(self, enable_gift_cards):
        from giftcard.tasks import send_gift_card_expiry_reminders

        card = GiftCardFactory(
            initial_value=Money(Decimal(30), "EUR"),
            recipient_email="soon@example.com",
            expires_at=timezone.now() + timezone.timedelta(days=10),
        )
        far_card = GiftCardFactory(
            initial_value=Money(Decimal(30), "EUR"),
            recipient_email="later@example.com",
            expires_at=timezone.now() + timezone.timedelta(days=300),
        )

        def _get(key, default=None):
            return {"GIFT_CARD_EXPIRY_REMINDER_DAYS": 30}.get(key, default)

        with patch("extra_settings.models.Setting.get", side_effect=_get):
            first = send_gift_card_expiry_reminders.apply().result
            second = send_gift_card_expiry_reminders.apply().result

        card.refresh_from_db()
        far_card.refresh_from_db()
        assert first["sent"] == 1
        assert second["sent"] == 0
        assert card.expiry_reminder_sent_at is not None
        assert far_card.expiry_reminder_sent_at is None

    def test_zero_setting_disables_reminders(self, enable_gift_cards):
        from giftcard.tasks import send_gift_card_expiry_reminders

        GiftCardFactory(
            recipient_email="soon@example.com",
            expires_at=timezone.now() + timezone.timedelta(days=5),
        )

        def _get(key, default=None):
            return {"GIFT_CARD_EXPIRY_REMINDER_DAYS": 0}.get(key, default)

        with patch("extra_settings.models.Setting.get", side_effect=_get):
            result = send_gift_card_expiry_reminders.apply().result

        assert result["reason"] == "disabled"


class TestVivaPurchaseWebhookBranch:
    def _event(self, purchase, event_type_id, status_id="F"):
        from order.views.viva_webhook import (
            _process_gift_card_purchase_event,
        )

        return _process_gift_card_purchase_event(
            purchase=purchase,
            event_type_id=event_type_id,
            event_data={"StatusId": status_id},
            transaction_id=f"txn-{purchase.pk}-{event_type_id}",
            txn_hash="deadbeef",
            order_code=purchase.payment_id,
        )

    def test_payment_created_completes_purchase(self, enable_gift_cards):
        from order.enum.status import PaymentStatus

        purchase = GiftCardPurchaseFactory(
            amount=Money(Decimal(40), "EUR"),
            provider_code="viva_wallet",
        )
        purchase.payment_id = "1234567890123456"
        purchase.save(update_fields=["payment_id"])

        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(
                PaymentStatus.COMPLETED,
                {"amount": "40.00", "order_code": purchase.payment_id},
            ),
        ):
            response = self._event(purchase, 1796)

        purchase.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.PAID
        assert purchase.gift_cards.count() == 1

    def test_amount_mismatch_refuses_completion(self, enable_gift_cards):
        from order.enum.status import PaymentStatus

        purchase = GiftCardPurchaseFactory(
            amount=Money(Decimal(40), "EUR"),
            provider_code="viva_wallet",
        )
        purchase.payment_id = "2234567890123456"
        purchase.save(update_fields=["payment_id"])

        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(
                PaymentStatus.COMPLETED,
                {"amount": "5.00", "order_code": purchase.payment_id},
            ),
        ):
            response = self._event(purchase, 1796)

        purchase.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.PENDING
        assert purchase.gift_cards.count() == 0

    def test_unverifiable_transaction_returns_500_for_redelivery(
        self, enable_gift_cards
    ):
        purchase = GiftCardPurchaseFactory(
            amount=Money(Decimal(40), "EUR"),
            provider_code="viva_wallet",
        )
        purchase.payment_id = "3234567890123456"
        purchase.save(update_fields=["payment_id"])

        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(None, {"error": "boom", "viva_error": True}),
        ):
            response = self._event(purchase, 1796)

        assert response.status_code == 500
        purchase.refresh_from_db()
        assert purchase.status == GiftCardPurchaseStatus.PENDING

    def _paid_purchase(self, payment_id):
        """A PAID purchase with one issued, untouched card."""
        purchase = GiftCardPurchaseFactory(
            amount=Money(Decimal(40), "EUR"), provider_code="viva_wallet"
        )
        purchase.payment_id = payment_id
        purchase.save(update_fields=["payment_id"])
        card = GiftCardService.complete_purchase(purchase, "txn-paid")
        return purchase, card

    def test_reversal_without_a_verified_refund_voids_nothing(
        self, enable_gift_cards
    ):
        """The endpoint is unauthenticated and the Viva order code is
        visible to the buyer, so an unverified 1797 must not be able to
        cancel a purchase and disable the card it paid for."""
        from order.enum.status import PaymentStatus

        purchase, card = self._paid_purchase("5234567890123456")

        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(PaymentStatus.COMPLETED, {"amount": "40.00"}),
        ):
            response = self._event(purchase, 1797)

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.PAID
        assert card.status == GiftCardStatus.ACTIVE
        assert card.balance.amount == Decimal(40)

    def test_reversal_without_a_transaction_id_voids_nothing(
        self, enable_gift_cards
    ):
        from order.views.viva_webhook import (
            _process_gift_card_purchase_event,
        )

        purchase, card = self._paid_purchase("6234567890123456")

        response = _process_gift_card_purchase_event(
            purchase=purchase,
            event_type_id=1797,
            event_data={"StatusId": "F"},
            transaction_id="",
            txn_hash="deadbeef",
            order_code=purchase.payment_id,
        )

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.PAID
        assert card.status == GiftCardStatus.ACTIVE

    def test_verified_reversal_voids_the_untouched_card(
        self, enable_gift_cards
    ):
        from order.enum.status import PaymentStatus

        purchase, card = self._paid_purchase("7234567890123456")

        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(PaymentStatus.REFUNDED, {"amount": "40.00"}),
        ):
            response = self._event(purchase, 1797)

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.CANCELED
        assert card.status == GiftCardStatus.DISABLED
        assert card.balance.amount == Decimal(0)

    def test_unverifiable_reversal_returns_500_and_voids_nothing(
        self, enable_gift_cards
    ):
        purchase, card = self._paid_purchase("8234567890123456")

        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(None, {"error": "boom", "viva_error": True}),
        ):
            response = self._event(purchase, 1797)

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert response.status_code == 500
        assert purchase.status == GiftCardPurchaseStatus.PAID
        assert card.status == GiftCardStatus.ACTIVE

    def test_payment_failed_needs_a_verified_failure(self, enable_gift_cards):
        """A 1798 whose transaction actually succeeded must not flip a
        purchase to FAILED under the buyer's feet."""
        from order.enum.status import PaymentStatus

        purchase = GiftCardPurchaseFactory(provider_code="viva_wallet")
        purchase.payment_id = "9234567890123456"
        purchase.save(update_fields=["payment_id"])

        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(PaymentStatus.COMPLETED, {}),
        ):
            response = self._event(purchase, 1798, status_id="")

        purchase.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.PENDING

    def test_payment_failed_marks_failed(self, enable_gift_cards):
        from order.enum.status import PaymentStatus

        purchase = GiftCardPurchaseFactory(provider_code="viva_wallet")
        purchase.payment_id = "4234567890123456"
        purchase.save(update_fields=["payment_id"])

        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(PaymentStatus.FAILED, {}),
        ):
            response = self._event(purchase, 1798, status_id="")

        purchase.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.FAILED
