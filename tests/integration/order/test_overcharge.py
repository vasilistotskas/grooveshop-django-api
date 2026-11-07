"""A second successful payment on one order is an overcharge, and says so.

Two verified Viva payments could land on one order (the shopper pays on two
checkout codes, or on two tabs). Each was booked as a CHARGE, which is
right: the money moved. But the webhook then overwrote ``Order.payment_id``
with the second transaction and answered success, and nothing compared what
the ledger holds with what the order is for, so the customer was charged
twice and nobody was told.

``PaymentLedger.record_charge`` is the one writer of a provider charge. It
stamps ``Order.overcharged_at`` when the ledger holds more than the order
owes, sends one ops email, and a refund of the excess clears the stamp. The
first ``payment_id`` is kept. Nothing is refunded automatically.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.utils import timezone
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory
from order.models.history import OrderHistory
from order.models.order import Order
from order.payment_ledger import PaymentLedger
from order.services import OrderService
from order.signals.handlers import handle_stripe_checkout_completed
from order.views.viva_webhook import _handle_payment_created
from product.factories.product import ProductFactory
from tests.utils.viva import verified_payment

pytestmark = pytest.mark.django_db

OWED = Decimal("40.00")


def _order(**extra) -> Order:
    """A pending online order that owes exactly ``OWED``."""
    order = OrderFactory.create(
        num_order_items=0,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        shipping_price=Money(0, "EUR"),
        metadata={"viva_order_codes": ["OC1", "OC2"]},
        **extra,
    )
    order.items.create(
        product=ProductFactory(stock=10, num_images=0, num_reviews=0),
        price=Money(OWED, "EUR"),
        quantity=1,
    )
    Order.objects.filter(pk=order.pk).update(
        amount_due=Money(OWED, "EUR"), payment_id=""
    )
    order.refresh_from_db()
    assert order.calculate_order_total_amount().amount == OWED
    return order


def _viva(order, transaction_id, order_code="OC1"):
    """Deliver a verified Viva payment-created event for *order*."""
    with (
        patch(
            "order.views.viva_webhook._verify_transaction",
            return_value=(
                PaymentStatus.COMPLETED,
                verified_payment(order, order_code),
            ),
        ),
        patch("order.views.viva_webhook.dispatch_on_commit"),
        patch(
            "shipping.services.ShippingService.dispatch_create_shipment_task"
        ),
    ):
        _handle_payment_created(order, {"StatusId": "F"}, transaction_id)
    order.refresh_from_db()


def _stripe_checkout(order, payment_intent_id, session_id):
    event = type(
        "Event",
        (),
        {
            "id": f"evt_{session_id}",
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": session_id,
                    "payment_intent": payment_intent_id,
                    "payment_status": "paid",
                    "amount_total": int(OWED * 100),
                    "currency": "eur",
                    "metadata": {"order_id": str(order.id)},
                }
            },
        },
    )()
    with patch(
        "shipping.services.ShippingService.dispatch_create_shipment_task"
    ):
        handle_stripe_checkout_completed(sender=None, event=event)
    order.refresh_from_db()


def _alerts(send_email):
    return [
        c for c in send_email.call_args_list if c.args[0] == "ops.overcharged"
    ]


@pytest.fixture
def send_email():
    with patch("emails.send.send_email") as mocked:
        yield mocked


class TestTwoVivaPaymentsOnOneOrder:
    def test_the_second_payment_flags_the_order_and_keeps_the_first_id(
        self, send_email
    ):
        order = _order()

        _viva(order, "viva_txn_a", "OC1")
        assert order.payment_id == "viva_txn_a"
        assert order.overcharged_at is None

        _viva(order, "viva_txn_b", "OC2")

        assert order.payment_id == "viva_txn_a"
        assert order.overcharged_at is not None
        assert PaymentLedger.captured(order) == OWED * 2
        assert len(_alerts(send_email)) == 1
        notes = OrderHistory.objects.filter(order=order, change_type="NOTE")
        assert any("viva_txn_b" in str(n.new_value) for n in notes)

    def test_a_redelivery_does_not_alert_twice(self, send_email):
        order = _order()
        _viva(order, "viva_txn_a", "OC1")
        _viva(order, "viva_txn_b", "OC2")

        _viva(order, "viva_txn_b", "OC2")
        _viva(order, "viva_txn_a", "OC1")

        assert len(_alerts(send_email)) == 1
        assert order.payment_id == "viva_txn_a"

    def test_the_alert_names_the_excess(self, send_email):
        order = _order()
        _viva(order, "viva_txn_a", "OC1")
        _viva(order, "viva_txn_b", "OC2")

        [call] = _alerts(send_email)
        alert = call.args[1]
        assert alert.order_id == order.id
        assert alert.payment_id == "viva_txn_b"
        assert alert.excess == Money(OWED, "EUR")

    def test_a_single_payment_is_not_an_overcharge(self, send_email):
        order = _order()

        _viva(order, "viva_txn_a", "OC1")

        assert order.overcharged_at is None
        assert _alerts(send_email) == []


class TestTwoStripePaymentsOnOneOrder:
    def test_the_second_checkout_flags_the_order_and_keeps_the_first_id(
        self, send_email
    ):
        order = _order()

        _stripe_checkout(order, "pi_first", "cs_first")
        assert order.payment_id == "pi_first"

        _stripe_checkout(order, "pi_second", "cs_second")

        assert order.payment_id == "pi_first"
        assert order.overcharged_at is not None
        assert len(_alerts(send_email)) == 1


class TestTheFlagFollowsTheLedger:
    def _overcharged(self):
        order = _order()
        _viva(order, "viva_txn_a", "OC1")
        _viva(order, "viva_txn_b", "OC2")
        assert order.overcharged_at is not None
        return order

    def _refund(self, order, amount, reference, *, of="viva_txn_b"):
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE, provider_reference=of
        )
        with patch("emails.send.send_email"):
            PaymentLedger.record_refund(
                order,
                charge=charge,
                amount=Money(amount, "EUR"),
                reference=reference,
            )
        order.refresh_from_db()

    def test_refunding_the_excess_clears_it(self):
        order = self._overcharged()

        self._refund(order, OWED, "re_excess")

        assert (
            PaymentLedger.captured(order) - PaymentLedger.refunded(order)
            == OWED
        )
        assert order.overcharged_at is None

    def test_refunding_part_of_the_excess_keeps_it(self):
        order = self._overcharged()

        self._refund(order, Decimal("15.00"), "re_part")

        assert order.overcharged_at is not None

    def test_a_failed_refund_of_the_excess_flags_it_again(self):
        order = self._overcharged()
        self._refund(order, OWED, "re_excess")
        assert order.overcharged_at is None

        charge = order.payment_transactions.get(provider_reference="viva_txn_b")
        with patch("emails.send.send_email"):
            PaymentLedger.record_refund_reversal(
                order,
                charge=charge,
                amount=Money(OWED, "EUR"),
                reference="rev_excess",
            )
        order.refresh_from_db()

        assert order.overcharged_at is not None


class TestNormalOrdersAreNeverFlagged:
    @pytest.mark.parametrize("refunded", ["10.00", "25.00", "40.00"])
    def test_partial_and_full_refunds_do_not_flag(self, send_email, refunded):
        order = _order()
        _viva(order, "viva_txn_a", "OC1")
        charge = order.payment_transactions.get()

        PaymentLedger.record_refund(
            order,
            charge=charge,
            amount=Money(Decimal(refunded), "EUR"),
            reference="re_normal",
        )
        order.refresh_from_db()

        assert order.overcharged_at is None
        assert _alerts(send_email) == []

    def test_a_charge_within_the_rounding_tolerance_does_not_flag(
        self, send_email
    ):
        order = _order()

        PaymentLedger.record_charge(
            order,
            amount=Money(OWED + Decimal("0.01"), "EUR"),
            provider="viva_wallet",
            reference="viva_cent",
        )

        assert order.overcharged_at is None
        assert _alerts(send_email) == []

    def test_a_charge_on_a_canceled_order_is_the_other_alert(self, send_email):
        order = _order()
        Order.objects.filter(pk=order.pk).update(
            status=OrderStatus.CANCELED, payment_status=PaymentStatus.CANCELED
        )
        order.refresh_from_db()

        PaymentLedger.record_charge(
            order,
            amount=Money(OWED, "EUR"),
            provider="viva_wallet",
            reference="viva_late",
        )
        OrderService.record_payment_after_cancel(
            order, payment_id="viva_late", payment_method="viva_wallet"
        )

        assert order.overcharged_at is None
        assert _alerts(send_email) == []


def test_the_flag_has_a_moment():
    order = _order()
    _viva(order, "viva_txn_a", "OC1")
    before = timezone.now()
    _viva(order, "viva_txn_b", "OC2")

    assert order.overcharged_at >= before
