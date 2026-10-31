"""A gift-card purchase's payment is taken back: the card goes with it.

Only a plain Viva refund used to reach ``GiftCardService``. A Viva refund
claim, a Viva dispute (2054) and every Stripe refund and dispute looked an
ORDER up, found none and did nothing, so a buyer who charged the payment
back kept a spendable card. Also pinned here: the Viva 1796 completion of a
purchase is bound to the purchase's own order code, currency and amount.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from djmoney.money import Money
from djstripe.models import Event

from giftcard.enum import (
    GiftCardPurchaseStatus,
    GiftCardStatus,
    GiftCardTransactionKind,
)
from giftcard.factories import GiftCardPurchaseFactory
from giftcard.models import GiftCardTransaction
from giftcard.services import GiftCardService
from order.enum.status import PaymentStatus
from order.signals.handlers import (
    handle_stripe_charge_refunded,
    handle_stripe_dispute_closed,
    handle_stripe_dispute_created,
)
from order.views.viva_webhook import (
    VIVA_ACCOUNT_TRANSACTION_CREATED,
    _process_dispute_event_in_tenant,
    _process_gift_card_purchase_event,
)
from tests.utils.viva import (
    REVERSAL_CREATED_EVENT,
    FakeVivaApi,
    retrieve_refund,
    retrieve_transaction,
    reversal_created_event,
)

pytestmark = pytest.mark.django_db

ORDER_CODE = "8234567890123456"
SALE_ID = "sale-gc-1"
INTENT = "pi_gc_reversal"


@pytest.fixture(autouse=True)
def _gift_cards(enable_gift_cards):
    return None


@pytest.fixture
def alerts():
    with patch("emails.send.send_email") as alert:
        yield alert


def _viva_purchase(order_code=ORDER_CODE):
    purchase = GiftCardPurchaseFactory(
        amount=Money(Decimal(40), "EUR"), provider_code="viva_wallet"
    )
    purchase.payment_id = order_code
    purchase.save(update_fields=["payment_id"])
    return purchase


def _paid(purchase, *, transaction_id=SALE_ID):
    card = GiftCardService.complete_purchase(
        purchase, transaction_id=transaction_id
    )
    return card


def _spend(card, amount):
    GiftCardTransaction.objects.create(
        gift_card=card,
        kind=GiftCardTransactionKind.REDEEM,
        amount=-Decimal(amount),
    )


def _stripe_purchase():
    purchase = GiftCardPurchaseFactory(
        amount=Money(Decimal(40), "EUR"), provider_code="stripe"
    )
    purchase.payment_id = INTENT
    purchase.save(update_fields=["payment_id"])
    card = GiftCardService.complete_purchase(purchase, payment_id=INTENT)
    return purchase, card


def _event(event_type, data):
    event = Mock(spec=Event)
    event.id = f"evt_{event_type}_{data.get('id', 'x')}"
    event.type = event_type
    event.data = {"object": data}
    return event


class TestStripeReversals:
    def test_a_full_refund_voids_the_card(self):
        purchase, card = _stripe_purchase()

        handle_stripe_charge_refunded(
            sender=None,
            event=_event(
                "charge.refunded",
                {
                    "id": "ch_1",
                    "payment_intent": INTENT,
                    "currency": "eur",
                    "amount": 4000,
                    "amount_refunded": 4000,
                },
            ),
        )

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert purchase.status == GiftCardPurchaseStatus.CANCELED
        assert card.status == GiftCardStatus.DISABLED
        assert card.balance.amount == 0

    def test_a_part_refund_leaves_the_card_alone(self):
        purchase, card = _stripe_purchase()

        handle_stripe_charge_refunded(
            sender=None,
            event=_event(
                "charge.refunded",
                {
                    "id": "ch_1",
                    "payment_intent": INTENT,
                    "currency": "eur",
                    "amount": 4000,
                    "amount_refunded": 1000,
                },
            ),
        )

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert purchase.refunded_amount == Decimal(10)
        assert purchase.status == GiftCardPurchaseStatus.PAID
        assert card.status == GiftCardStatus.ACTIVE

    def test_a_lost_dispute_voids_what_is_left_and_records_what_was_spent(
        self, alerts
    ):
        purchase, card = _stripe_purchase()
        _spend(card, 15)

        handle_stripe_dispute_closed(
            sender=None,
            event=_event(
                "charge.dispute.closed",
                {
                    "id": "dp_1",
                    "payment_intent": INTENT,
                    "status": "lost",
                    "amount": 4000,
                    "currency": "eur",
                },
            ),
        )

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert card.status == GiftCardStatus.DISABLED
        assert card.balance.amount == 0
        assert purchase.unrecovered_amount == Decimal(15)
        alerts.assert_called_once()

    def test_a_won_dispute_changes_nothing(self):
        _purchase, card = _stripe_purchase()

        handle_stripe_dispute_closed(
            sender=None,
            event=_event(
                "charge.dispute.closed",
                {
                    "id": "dp_1",
                    "payment_intent": INTENT,
                    "status": "won",
                    "amount": 4000,
                    "currency": "eur",
                },
            ),
        )

        card.refresh_from_db()
        assert card.status == GiftCardStatus.ACTIVE

    def test_an_opened_dispute_tells_staff_and_voids_nothing_yet(self, alerts):
        _purchase, card = _stripe_purchase()

        handle_stripe_dispute_created(
            sender=None,
            event=_event(
                "charge.dispute.created",
                {
                    "id": "dp_1",
                    "charge": "ch_1",
                    "payment_intent": INTENT,
                    "reason": "fraudulent",
                    "status": "needs_response",
                },
            ),
        )

        card.refresh_from_db()
        assert card.status == GiftCardStatus.ACTIVE
        alerts.assert_called_once()


class TestRedeliveredEvents:
    """A provider delivers an event more than once (retries, parallel
    deliveries): staff are told once and the card is voided once."""

    def _dispute_created(self):
        handle_stripe_dispute_created(
            sender=None,
            event=_event(
                "charge.dispute.created",
                {
                    "id": "dp_1",
                    "charge": "ch_1",
                    "payment_intent": INTENT,
                    "reason": "fraudulent",
                    "status": "needs_response",
                },
            ),
        )

    def test_a_redelivered_dispute_alerts_staff_once(self, alerts):
        _stripe_purchase()

        self._dispute_created()
        self._dispute_created()

        alerts.assert_called_once()

    def test_another_dispute_of_the_same_purchase_alerts_again(self, alerts):
        purchase, _card = _stripe_purchase()

        self._dispute_created()
        purchase.refresh_from_db()
        purchase.dispute_id = "dp_earlier"
        purchase.save(update_fields=["dispute_id"])
        self._dispute_created()

        assert alerts.call_count == 2

    def test_a_redelivered_lost_dispute_voids_and_alerts_once(self, alerts):
        _purchase, card = _stripe_purchase()
        _spend(card, 15)
        event = _event(
            "charge.dispute.closed",
            {
                "id": "dp_1",
                "payment_intent": INTENT,
                "status": "lost",
                "amount": 4000,
                "currency": "eur",
            },
        )

        handle_stripe_dispute_closed(sender=None, event=event)
        handle_stripe_dispute_closed(sender=None, event=event)

        alerts.assert_called_once()
        assert (
            card.transactions.filter(
                kind=GiftCardTransactionKind.ADJUST
            ).count()
            == 1
        )

    def test_viva_reports_one_dispute_through_several_statuses_once(
        self, alerts
    ):
        purchase = _viva_purchase()
        _paid(purchase)
        for transaction_id, status_id in (("acct-1", "M"), ("acct-2", "MI")):
            sale = retrieve_transaction(
                orderCode=int(ORDER_CODE), statusId=status_id, amount=40.0
            )
            with FakeVivaApi(retrieve=(200, sale)):
                _process_dispute_event_in_tenant(
                    sale_id=SALE_ID, transaction_id=transaction_id
                )

        alerts.assert_called_once()


class TestVivaReversals:
    def _claim(self, purchase):
        body = reversal_created_event(
            refund_id="claim-1",
            parent_id=REVERSAL_CREATED_EVENT["EventData"]["ParentId"],
            order_code=int(purchase.payment_id),
        )
        return _process_gift_card_purchase_event(
            purchase=purchase,
            event_type_id=body["EventTypeId"],
            event_data=body["EventData"],
            transaction_id="claim-1",
            txn_hash="deadbeef",
            order_code=purchase.payment_id,
        )

    def test_a_refund_claim_voids_the_card_whatever_it_names(self):
        purchase = _viva_purchase()
        card = _paid(purchase)
        # A claim of only EUR 5: still a chargeback of the purchase.
        claim = retrieve_refund(
            amount=5.0, order_code=int(ORDER_CODE), type_id=13
        )

        with FakeVivaApi(retrieve=(200, claim)):
            response = self._claim(purchase)

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.CANCELED
        assert card.status == GiftCardStatus.DISABLED

    def _dispute(self, status_id, order_code=ORDER_CODE):
        sale = retrieve_transaction(
            orderCode=int(order_code), statusId=status_id, amount=40.0
        )
        with FakeVivaApi(retrieve=(200, sale)):
            return _process_dispute_event_in_tenant(
                sale_id=SALE_ID, transaction_id="acct-1"
            )

    def test_a_lost_dispute_voids_the_card(self):
        purchase = _viva_purchase()
        card = _paid(purchase)

        response = self._dispute("ML")

        purchase.refresh_from_db()
        card.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.CANCELED
        assert card.status == GiftCardStatus.DISABLED

    def test_an_opened_dispute_tells_staff_and_voids_nothing_yet(self, alerts):
        purchase = _viva_purchase()
        card = _paid(purchase)

        self._dispute("MI")

        card.refresh_from_db()
        assert card.status == GiftCardStatus.ACTIVE
        alerts.assert_called_once()

    def test_a_dispute_of_a_sale_that_is_not_the_purchases_is_ignored(self):
        purchase = _viva_purchase()
        card = _paid(purchase, transaction_id="another-sale")

        self._dispute("ML")

        card.refresh_from_db()
        assert card.status == GiftCardStatus.ACTIVE

    def test_the_dispute_webhook_finds_the_purchase_by_its_sale(self):
        from order.views.viva_webhook import _tenants_holding_sale

        purchase = _viva_purchase()
        _paid(purchase)
        tenant = Mock(schema_name="public")

        with patch("order.views.viva_webhook.schema_context"):
            assert _tenants_holding_sale([tenant], SALE_ID) == [tenant]
            assert _tenants_holding_sale([tenant], "unknown-sale") == []

    def test_the_event_is_recorded_once(self):
        from order.models.viva_webhook_event import VivaWebhookEvent

        purchase = _viva_purchase()
        _paid(purchase)

        self._dispute("ML")
        self._dispute("ML")

        assert (
            VivaWebhookEvent.objects.filter(
                transaction_id="acct-1",
                event_type_id=VIVA_ACCOUNT_TRANSACTION_CREATED,
            ).count()
            == 1
        )


class TestCompletionIsBoundToThePurchase:
    def _complete(self, purchase, data):
        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(PaymentStatus.COMPLETED, data),
        ):
            return _process_gift_card_purchase_event(
                purchase=purchase,
                event_type_id=1796,
                event_data={"StatusId": "F"},
                transaction_id="txn-bound",
                txn_hash="deadbeef",
                order_code=purchase.payment_id,
            )

    def _data(self, **overrides):
        return {
            "amount": "40.00",
            "currency": "EUR",
            "order_code": ORDER_CODE,
            **overrides,
        }

    def test_the_purchases_own_transaction_completes_it(self):
        purchase = _viva_purchase()

        response = self._complete(purchase, self._data())

        purchase.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.PAID
        assert purchase.provider_transaction_id == "txn-bound"

    @pytest.mark.parametrize(
        "overrides",
        [
            {"order_code": "9999999999999999"},
            {"currency": "USD"},
            {"currency": None},
            {"amount": None},
            {"amount": "5.00"},
        ],
        ids=[
            "another order code",
            "another currency",
            "no currency",
            "no amount",
            "another amount",
        ],
    )
    def test_anything_else_mints_no_card(self, overrides):
        purchase = _viva_purchase()

        response = self._complete(purchase, self._data(**overrides))

        purchase.refresh_from_db()
        assert response.status_code == 200
        assert purchase.status == GiftCardPurchaseStatus.PENDING
        assert not purchase.gift_cards.exists()

    def test_a_transaction_that_reports_no_order_code_is_retried(self):
        purchase = _viva_purchase()

        response = self._complete(purchase, self._data(order_code=None))

        assert response.status_code == 500
        purchase.refresh_from_db()
        assert purchase.status == GiftCardPurchaseStatus.PENDING
