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
from tests.utils.viva import (
    REVERSAL_CREATED_EVENT,
    FakeVivaApi,
    retrieve_refund,
    retrieve_transaction,
    reversal_created_event,
)

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
        """A PAID purchase with one issued, untouched card.

        Completed the way the Viva webhook completes it: the purchase keeps
        the Viva order code it was paid under (as ``payment_id``).
        """
        purchase = GiftCardPurchaseFactory(
            amount=Money(Decimal(40), "EUR"), provider_code="viva_wallet"
        )
        purchase.payment_id = payment_id
        purchase.save(update_fields=["payment_id"])
        card = GiftCardService.complete_purchase(purchase)
        return purchase, card

    def _reversal(self, purchase, refund_id=None):
        """Viva's documented 1797 for a refund of this purchase's payment."""
        from order.views.viva_webhook import (
            _process_gift_card_purchase_event,
        )

        refund_id = refund_id or f"refund-{purchase.pk}"
        body = reversal_created_event(
            refund_id=refund_id,
            parent_id=REVERSAL_CREATED_EVENT["EventData"]["ParentId"],
            order_code=int(purchase.payment_id),
        )
        return _process_gift_card_purchase_event(
            purchase=purchase,
            event_type_id=body["EventTypeId"],
            event_data=body["EventData"],
            transaction_id=refund_id,
            txn_hash="deadbeef",
            order_code=purchase.payment_id,
        )

    def test_reversal_without_a_verified_refund_voids_nothing(
        self, enable_gift_cards
    ):
        """The endpoint is unauthenticated and the Viva order code is
        visible to the buyer, so a 1797 quoting a transaction that is not
        a refund (Viva's documented ``F`` payment) must not be able to
        cancel a purchase and disable the card it paid for."""
        purchase, card = self._paid_purchase("5234567890123456")
        payment = retrieve_transaction(orderCode=int(purchase.payment_id))

        with FakeVivaApi(retrieve=(200, payment)):
            response = self._reversal(purchase)

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
        purchase, card = self._paid_purchase("7234567890123456")
        refund = retrieve_refund(
            amount=40.0, order_code=int(purchase.payment_id)
        )

        with FakeVivaApi(retrieve=(200, refund)):
            response = self._reversal(purchase)

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.CANCELED
        assert card.status == GiftCardStatus.DISABLED
        assert card.balance.amount == Decimal(0)

    def test_a_part_refund_does_not_void_value_the_buyer_still_owns(
        self, enable_gift_cards
    ):
        """The card is the whole purchase; a goodwill refund of EUR 5 of
        EUR 40 must not destroy the other EUR 35. It is booked, though:
        the next refunds add to it."""
        from order.models.viva_webhook_event import VivaWebhookEvent

        purchase, card = self._paid_purchase("9034567890123456")
        refund = retrieve_refund(
            amount=5.0, order_code=int(purchase.payment_id)
        )

        with FakeVivaApi(retrieve=(200, refund)):
            response = self._reversal(purchase)

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.PAID
        assert purchase.refunded_amount == Decimal(5)
        assert card.status == GiftCardStatus.ACTIVE
        assert card.balance.amount == Decimal(40)
        assert (
            VivaWebhookEvent.objects.get(
                transaction_id=f"refund-{purchase.pk}"
            ).outcome
            == VivaWebhookEvent.OUTCOME_PROCESSED
        )

    def _viva_refunding(self, purchase, **refunds):
        """A Viva that answers Retrieve for each named refund id."""
        return FakeVivaApi(
            transactions={
                refund_id: (
                    200,
                    retrieve_refund(
                        amount=paid_back, order_code=int(purchase.payment_id)
                    ),
                )
                for refund_id, paid_back in refunds.items()
            }
        )

    def test_refunds_that_add_up_to_the_purchase_void_the_card(
        self, enable_gift_cards
    ):
        """EUR 5 and then EUR 35 of a EUR 40 purchase: the first leaves the
        card spendable, the second completes the refund and voids it."""
        purchase, card = self._paid_purchase("9234567890123457")

        with self._viva_refunding(purchase, first=5.0, second=35.0):
            self._reversal(purchase, refund_id="first")
            card.refresh_from_db()
            assert card.status == GiftCardStatus.ACTIVE
            assert card.balance.amount == Decimal(40)
            self._reversal(purchase, refund_id="second")

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert purchase.refunded_amount == Decimal(40)
        assert purchase.status == GiftCardPurchaseStatus.CANCELED
        assert card.status == GiftCardStatus.DISABLED
        assert card.balance.amount == Decimal(0)

    def test_a_replayed_refund_is_counted_once(self, enable_gift_cards):
        """Viva redelivers: EUR 5 delivered twice is EUR 5, not EUR 10, and
        must not bring the card closer to being voided."""
        purchase, card = self._paid_purchase("9334567890123458")

        with self._viva_refunding(purchase, first=5.0, second=35.0):
            self._reversal(purchase, refund_id="first")
            replay = self._reversal(purchase, refund_id="first")
            # Acknowledged and left alone, not refused with a 500 that
            # Viva would keep redelivering.
            assert replay.status_code == 200
            purchase.refresh_from_db()
            assert purchase.refunded_amount == Decimal(5)
            self._reversal(purchase, refund_id="second")

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert purchase.refunded_amount == Decimal(40)
        assert card.status == GiftCardStatus.DISABLED

    def test_a_refund_of_another_payment_voids_nothing(self, enable_gift_cards):
        """Anyone may post their OWN real refund against this purchase's
        order code; Viva's instruction is to confirm OrderCode AND
        TransactionId."""
        purchase, card = self._paid_purchase("9134567890123456")
        refund = retrieve_refund(amount=40.0, order_code=1234567890123456)

        with FakeVivaApi(retrieve=(200, refund)):
            response = self._reversal(purchase)

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.PAID
        assert card.status == GiftCardStatus.ACTIVE

    def test_unverifiable_reversal_returns_500_and_voids_nothing(
        self, enable_gift_cards
    ):
        purchase, card = self._paid_purchase("8234567890123456")

        with FakeVivaApi(retrieve=(404, ["Transaction Id does not exist."])):
            response = self._reversal(purchase)

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
