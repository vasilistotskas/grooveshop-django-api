"""What a provider charged for a payment, on the ledger row.

A fee is the provider's own figure or nothing: a payment whose provider has
not reported one stays "not reported" (no fee, no source), never zero and
never an estimate. Viva reports it in a "Transaction Price Calculated" event
(1799), Stripe on the charge's balance transaction.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import stripe
from django.db import IntegrityError, transaction
from djmoney.money import Money
from djstripe.models import Event

from order.enum.payment_transaction import PaymentFeeSource
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models import PaymentTransaction, PendingPaymentFee
from order.models.viva_webhook_event import VivaWebhookEvent
from order.payment import VivaWalletPaymentProvider
from order.payment_fees import (
    record_stripe_charge_fee,
    record_stripe_fee_for_intent,
)
from order.payment_ledger import PaymentLedger
from order.signals.handlers import handle_stripe_charge_fee
from order.views.viva_webhook import (
    VIVA_TRANSACTION_PRICE_CALCULATED,
    _handle_transaction_price_calculated,
    _process_event_in_tenant,
)

pytestmark = pytest.mark.django_db

D = Decimal
ORDER_CODE = "4254158847011355"
TRANSACTION_ID = "2c826011-7293-46fc-9abb-6fc7b5349286"


class TestRecordFee:
    def test_the_provider_figure_lands_on_its_row(self):
        row = PaymentTransactionFactory(provider_reference="pi_fee_1")

        PaymentLedger.record_fee(
            provider="stripe",
            reference="pi_fee_1",
            fee=Money(D("1.75"), "EUR"),
            source=PaymentFeeSource.STRIPE_BALANCE_TRANSACTION,
        )

        row.refresh_from_db()
        assert row.fee == Money(D("1.75"), "EUR")
        assert row.fee_source == PaymentFeeSource.STRIPE_BALANCE_TRANSACTION

    def test_a_payment_with_no_report_has_no_fee_and_no_source(self):
        row = PaymentTransactionFactory()

        row.refresh_from_db()
        assert row.fee is None
        assert row.fee_source == ""

    def test_a_later_figure_replaces_the_earlier_one(self):
        row = PaymentTransactionFactory(provider_reference="pi_fee_2")
        for fee in ("1.75", "1.80"):
            PaymentLedger.record_fee(
                provider="stripe",
                reference="pi_fee_2",
                fee=Money(D(fee), "EUR"),
                source=PaymentFeeSource.STRIPE_BALANCE_TRANSACTION,
            )

        row.refresh_from_db()
        assert row.fee == Money(D("1.80"), "EUR")

    def test_a_fee_for_a_payment_the_ledger_lacks_waits_for_it(self):
        order = OrderFactory(num_order_items=0)

        result = PaymentLedger.record_fee(
            provider="stripe",
            reference="pi_early",
            fee=Money(D("1.00"), "EUR"),
            source=PaymentFeeSource.STRIPE_BALANCE_TRANSACTION,
        )

        assert result is None
        assert PendingPaymentFee.objects.get().fee == Money(D("1.00"), "EUR")
        row, created = PaymentLedger.record_charge(
            order,
            amount=Money(D("50.00"), "EUR"),
            provider="stripe",
            reference="pi_early",
        )
        assert created
        row.refresh_from_db()
        assert row.fee == Money(D("1.00"), "EUR")
        assert row.fee_source == PaymentFeeSource.STRIPE_BALANCE_TRANSACTION
        assert not PendingPaymentFee.objects.exists()

    def test_a_waiting_fee_is_replaced_by_a_later_figure(self):
        order = OrderFactory(num_order_items=0)
        for fee in ("1.00", "1.20"):
            PaymentLedger.record_fee(
                provider="stripe",
                reference="pi_early_2",
                fee=Money(D(fee), "EUR"),
                source=PaymentFeeSource.STRIPE_BALANCE_TRANSACTION,
            )

        assert PendingPaymentFee.objects.count() == 1
        row, _created = PaymentLedger.record_charge(
            order,
            amount=Money(D("50.00"), "EUR"),
            provider="stripe",
            reference="PI_EARLY_2",
        )
        row.refresh_from_db()
        assert row.fee == Money(D("1.20"), "EUR")

    def test_a_waiting_fee_in_another_currency_is_dropped_not_converted(self):
        order = OrderFactory(num_order_items=0)
        PaymentLedger.record_fee(
            provider="stripe",
            reference="pi_early_usd",
            fee=Money(D("1.00"), "USD"),
            source=PaymentFeeSource.STRIPE_BALANCE_TRANSACTION,
        )

        row, _created = PaymentLedger.record_charge(
            order,
            amount=Money(D("50.00"), "EUR"),
            provider="stripe",
            reference="pi_early_usd",
        )

        row.refresh_from_db()
        assert row.fee is None
        assert not PendingPaymentFee.objects.exists()

    def test_a_waiting_fee_is_for_its_own_provider_only(self):
        order = OrderFactory(num_order_items=0)
        PaymentLedger.record_fee(
            provider="viva_wallet",
            reference="shared_ref",
            fee=Money(D("1.00"), "EUR"),
            source=PaymentFeeSource.VIVA_TRANSACTION_PRICE,
        )

        row, _created = PaymentLedger.record_charge(
            order,
            amount=Money(D("50.00"), "EUR"),
            provider="stripe",
            reference="shared_ref",
        )

        row.refresh_from_db()
        assert row.fee is None
        assert PendingPaymentFee.objects.count() == 1

    def test_a_fee_in_another_currency_is_not_recorded(self):
        row = PaymentTransactionFactory(provider_reference="pi_fee_usd")

        result = PaymentLedger.record_fee(
            provider="stripe",
            reference="pi_fee_usd",
            fee=Money(D("1.00"), "USD"),
            source=PaymentFeeSource.STRIPE_BALANCE_TRANSACTION,
        )

        assert result is None
        row.refresh_from_db()
        assert row.fee is None

    def test_a_negative_fee_is_refused(self):
        PaymentTransactionFactory(provider_reference="pi_fee_neg")

        with pytest.raises(ValueError, match="never negative"):
            PaymentLedger.record_fee(
                provider="stripe",
                reference="pi_fee_neg",
                fee=Money(D("-1.00"), "EUR"),
                source=PaymentFeeSource.STRIPE_BALANCE_TRANSACTION,
            )

    def test_the_database_refuses_a_fee_without_its_source(self):
        row = PaymentTransactionFactory()

        with pytest.raises(IntegrityError), transaction.atomic():
            PaymentTransaction.objects.filter(pk=row.pk).update(
                fee=Money(D("1.00"), "EUR")
            )


def _viva_order():
    order = OrderFactory(
        num_order_items=0,
        status=OrderStatus.PROCESSING,
        payment_status=PaymentStatus.COMPLETED,
        metadata={"viva_order_codes": [ORDER_CODE]},
    )
    row = PaymentTransactionFactory(
        order=order,
        provider=VivaWalletPaymentProvider.code,
        provider_reference=TRANSACTION_ID,
    )
    return order, row


def _event_data(**overrides):
    # Viva's documented sample for EventTypeId 1799
    # (developer.viva.com/webhooks-for-payments/transaction-price-calculated)
    data = {
        "OrderCode": int(ORDER_CODE),
        "IsvFee": 0,
        "TransactionId": TRANSACTION_ID,
        "CurrencyCode": "978",
        "Interchange": -0.015,
        "TotalCommission": 0.3,
    }
    data.update(overrides)
    return data


def _retrieved(order_code=ORDER_CODE):
    return patch(
        "order.views.viva_webhook._verify_transaction",
        return_value=(
            PaymentStatus.COMPLETED,
            {"order_code": order_code, "amount": 50.0, "currency": "EUR"},
        ),
    )


class TestVivaTransactionPriceCalculated:
    def test_the_total_commission_lands_on_the_payment(self):
        order, row = _viva_order()

        with _retrieved():
            outcome = _handle_transaction_price_calculated(
                order, _event_data(), TRANSACTION_ID
            )

        assert outcome is None
        row.refresh_from_db()
        assert row.fee == Money(D("0.30"), "EUR")
        assert row.fee_source == PaymentFeeSource.VIVA_TRANSACTION_PRICE

    def test_the_interchange_sign_in_the_sample_is_not_read(self):
        order, row = _viva_order()

        with _retrieved():
            _handle_transaction_price_calculated(
                order, _event_data(Interchange=0.015), TRANSACTION_ID
            )

        row.refresh_from_db()
        assert row.fee == Money(D("0.30"), "EUR")

    def test_a_transaction_of_another_order_records_nothing(self):
        order, row = _viva_order()

        with _retrieved(order_code="9999999999999999"):
            outcome = _handle_transaction_price_calculated(
                order, _event_data(), TRANSACTION_ID
            )

        assert outcome == VivaWebhookEvent.OUTCOME_SKIPPED
        row.refresh_from_db()
        assert row.fee is None

    def test_an_unverifiable_transaction_asks_viva_to_retry(self):
        order, row = _viva_order()

        with (
            patch(
                "order.views.viva_webhook._verify_transaction",
                return_value=(None, {}),
            ),
            pytest.raises(RuntimeError),
        ):
            _handle_transaction_price_calculated(
                order, _event_data(), TRANSACTION_ID
            )

        row.refresh_from_db()
        assert row.fee is None

    @pytest.mark.parametrize(
        "overrides",
        [
            {"TotalCommission": None},
            {"TotalCommission": "n/a"},
            {"TotalCommission": -0.3},
            {"CurrencyCode": None},
            {"CurrencyCode": "000"},
        ],
    )
    def test_an_unusable_commission_is_not_recorded_as_a_fee(self, overrides):
        order, row = _viva_order()

        with _retrieved():
            outcome = _handle_transaction_price_calculated(
                order, _event_data(**overrides), TRANSACTION_ID
            )

        assert outcome == VivaWebhookEvent.OUTCOME_SKIPPED
        row.refresh_from_db()
        assert row.fee is None

    def test_a_commission_before_the_payment_is_booked_is_kept(self):
        order, row = _viva_order()
        row.delete()

        with _retrieved():
            outcome = _handle_transaction_price_calculated(
                order, _event_data(), TRANSACTION_ID
            )

        assert outcome is None
        assert not PaymentTransaction.objects.filter(order=order).exists()
        assert PendingPaymentFee.objects.get().fee == Money(D("0.30"), "EUR")

    def test_the_payment_booked_after_its_commission_carries_it(self):
        # 1799 first, then 1796: both go through the webhook dispatch, so the
        # 1799 is recorded as handled and Viva never has to resend it.
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.PENDING,
            metadata={"viva_order_codes": [ORDER_CODE]},
        )
        with _retrieved():
            response = _process_event_in_tenant(
                order_code=ORDER_CODE,
                event_type_id=VIVA_TRANSACTION_PRICE_CALCULATED,
                event_data=_event_data(),
                transaction_id=TRANSACTION_ID,
                status_id="",
                txn_hash="",
            )
        assert response.status_code == 200
        [event] = VivaWebhookEvent.objects.filter(transaction_id=TRANSACTION_ID)
        assert event.outcome == VivaWebhookEvent.OUTCOME_PROCESSED

        with patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(
                PaymentStatus.COMPLETED,
                {
                    "order_code": ORDER_CODE,
                    "amount": float(order.total_price.amount),
                    "currency": "EUR",
                },
            ),
        ):
            response = _process_event_in_tenant(
                order_code=ORDER_CODE,
                event_type_id=1796,
                event_data={"TransactionId": TRANSACTION_ID},
                transaction_id=TRANSACTION_ID,
                status_id="F",
                txn_hash="",
            )
        assert response.status_code == 200

        charge = PaymentTransaction.objects.get(
            order=order, provider_reference=TRANSACTION_ID
        )
        assert charge.fee == Money(D("0.30"), "EUR")
        assert charge.fee_source == PaymentFeeSource.VIVA_TRANSACTION_PRICE
        assert not PendingPaymentFee.objects.exists()

    def test_a_commission_for_an_unknown_transaction_is_kept_nowhere(self):
        order, row = _viva_order()
        row.delete()

        with (
            patch(
                "order.views.viva_webhook._verify_transaction",
                return_value=(None, {}),
            ),
            pytest.raises(RuntimeError),
        ):
            _handle_transaction_price_calculated(
                order, _event_data(), TRANSACTION_ID
            )

        assert not PendingPaymentFee.objects.exists()

    def test_a_commission_of_another_orders_transaction_is_kept_nowhere(self):
        order, row = _viva_order()
        row.delete()

        with _retrieved(order_code="9999999999999999"):
            outcome = _handle_transaction_price_calculated(
                order, _event_data(), TRANSACTION_ID
            )

        assert outcome == VivaWebhookEvent.OUTCOME_SKIPPED
        assert not PendingPaymentFee.objects.exists()

    def test_the_event_goes_through_the_webhook_dispatch_once(self):
        _order, row = _viva_order()

        for _ in range(2):
            with _retrieved():
                response = _process_event_in_tenant(
                    order_code=ORDER_CODE,
                    event_type_id=VIVA_TRANSACTION_PRICE_CALCULATED,
                    event_data=_event_data(),
                    transaction_id=TRANSACTION_ID,
                    status_id="",
                    txn_hash="",
                )
            assert response.status_code == 200

        row.refresh_from_db()
        assert row.fee == Money(D("0.30"), "EUR")
        [event] = VivaWebhookEvent.objects.filter(transaction_id=TRANSACTION_ID)
        assert event.event_type_id == VIVA_TRANSACTION_PRICE_CALCULATED
        assert event.outcome == VivaWebhookEvent.OUTCOME_PROCESSED


def _charge(**overrides):
    charge = {
        "id": "ch_fee_1",
        "payment_intent": "pi_stripe_fee",
        "balance_transaction": "txn_fee_1",
        "currency": "eur",
    }
    charge.update(overrides)
    return charge


def _synced(fee, currency="eur", balance_transaction=True):
    bt = SimpleNamespace(fee=fee, currency=currency)
    return patch(
        "order.payment_fees.Charge.sync_from_stripe_data",
        return_value=SimpleNamespace(
            balance_transaction=bt if balance_transaction else None
        ),
    )


@pytest.fixture
def stripe_key():
    with patch(
        "order.payment_fees.stripe_credentials",
        return_value={"secret_key": "sk_test_x"},
    ):
        yield


@pytest.mark.usefixtures("stripe_key")
class TestStripeFee:
    def test_the_balance_transaction_fee_lands_on_the_payment(self):
        row = PaymentTransactionFactory(provider_reference="pi_stripe_fee")

        with _synced(175):
            record_stripe_charge_fee(_charge())

        row.refresh_from_db()
        assert row.fee == Money(D("1.75"), "EUR")
        assert row.fee_source == PaymentFeeSource.STRIPE_BALANCE_TRANSACTION

    def test_a_charge_with_no_balance_transaction_yet_records_nothing(self):
        row = PaymentTransactionFactory(provider_reference="pi_stripe_fee")

        with _synced(175) as sync:
            record_stripe_charge_fee(_charge(balance_transaction=None))

        sync.assert_not_called()
        row.refresh_from_db()
        assert row.fee is None

    def test_a_balance_transaction_that_has_not_synced_records_nothing(self):
        row = PaymentTransactionFactory(provider_reference="pi_stripe_fee")

        with _synced(175, balance_transaction=False):
            record_stripe_charge_fee(_charge())

        row.refresh_from_db()
        assert row.fee is None

    def test_a_charge_of_no_order_costs_no_stripe_call(self):
        with _synced(175) as sync:
            assert record_stripe_charge_fee(_charge()) is None

        sync.assert_not_called()

    def test_a_fee_settled_in_another_currency_is_left_unreported(self):
        row = PaymentTransactionFactory(provider_reference="pi_stripe_fee")

        with _synced(200, currency="usd"):
            record_stripe_charge_fee(_charge())

        row.refresh_from_db()
        assert row.fee is None

    def test_the_charge_event_handler_records_the_fee(self):
        row = PaymentTransactionFactory(provider_reference="pi_stripe_fee")
        event = Mock(spec=Event)
        event.data = {"object": _charge()}

        with (
            _synced(175),
            patch(
                "order.signals._tenant._resolve_event_schema",
                return_value="public",
            ),
        ):
            handle_stripe_charge_fee(sender=None, event=event)

        row.refresh_from_db()
        assert row.fee == Money(D("1.75"), "EUR")

    def test_a_booking_after_the_charge_events_still_gets_the_fee(self):
        """Stripe orders its events freely: the charge's came first and found
        no row, so the payment_intent that books it reads the fee itself."""
        row = PaymentTransactionFactory(provider_reference="pi_stripe_fee")

        with (
            patch(
                "order.payment_fees.stripe.Charge.retrieve",
                return_value=SimpleNamespace(to_dict=_charge),
            ) as retrieve,
            _synced(175),
        ):
            record_stripe_fee_for_intent("pi_stripe_fee", "ch_fee_1")

        retrieve.assert_called_once()
        row.refresh_from_db()
        assert row.fee == Money(D("1.75"), "EUR")

    def test_a_payment_that_already_has_its_fee_is_not_read_again(self):
        PaymentTransactionFactory(provider_reference="pi_stripe_fee")
        PaymentLedger.record_fee(
            provider="stripe",
            reference="pi_stripe_fee",
            fee=Money(D("1.75"), "EUR"),
            source=PaymentFeeSource.STRIPE_BALANCE_TRANSACTION,
        )

        with patch("order.payment_fees.stripe.Charge.retrieve") as retrieve:
            record_stripe_fee_for_intent("pi_stripe_fee", "ch_fee_1")

        retrieve.assert_not_called()

    def test_a_stripe_outage_does_not_break_the_booking(self):
        row = PaymentTransactionFactory(provider_reference="pi_stripe_fee")

        with patch(
            "order.payment_fees.stripe.Charge.retrieve",
            side_effect=stripe.APIConnectionError("down"),
        ):
            record_stripe_fee_for_intent("pi_stripe_fee", "ch_fee_1")

        row.refresh_from_db()
        assert row.fee is None
