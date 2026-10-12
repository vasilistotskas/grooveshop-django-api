"""``OrderService.refund_order``: one refund after another, until nothing is
left, and never past it (#136).

Only Stripe's refund endpoint is faked (``tests/utils/stripe_refunds.py``),
with its documented idempotency behaviour, so what these tests assert is
what would have been sent to Stripe: the amount, the PaymentIntent and the
``Idempotency-Key``.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

import pytest
import stripe
from django.db import connection
from django.test.utils import CaptureQueriesContext
from djmoney.money import Money

from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentError
from order.factories import OrderFactory, PaymentTransactionFactory
from order.models.history import OrderHistory
from order.models.order import Order
from order.payment_ledger import PaymentLedger
from order.services import OrderService
from pay_way.factories import PayWayFactory
from tests.utils.payments import CAPTURED
from tests.utils.stripe_refunds import FakeStripeRefunds

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

REFUND = PaymentTransactionKind.REFUND


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def _order(captured="100.00", **kwargs):
    """A delivered order Stripe captured *captured* for, as the ledger
    says."""
    kwargs.setdefault("pay_way", PayWayFactory.create_online_payment())
    kwargs.setdefault("status", OrderStatus.DELIVERED)
    kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
    kwargs.setdefault("payment_id", "pi_ledger")
    kwargs.setdefault("paid_amount", _eur(captured))
    order = OrderFactory(num_order_items=0, **kwargs)
    PaymentTransactionFactory(
        order=order, amount=_eur(captured), provider_reference="pi_ledger"
    )
    return order


def _status(order) -> str:
    return Order.objects.values_list("payment_status", flat=True).get(
        pk=order.pk
    )


def _refund_rows(order):
    return order.payment_transactions.filter(kind=REFUND)


class TestRefundingMoreThanOnce:
    def test_two_partial_refunds_then_the_rest(self, refunded_signals):
        order = _order("100.00")

        with FakeStripeRefunds() as stripe_api:
            first, _ = OrderService.refund_order(
                order, amount=_eur("30.00"), reason="one item back"
            )
            assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED
            second, _ = OrderService.refund_order(
                order, amount=_eur("25.00"), reason="another item back"
            )
            assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED
            # Nothing named: everything that is left, which is 45.
            final, _ = OrderService.refund_order(order, reason="the rest")

        assert (first, second, final) == (True, True, True)
        assert _status(order) == PaymentStatus.REFUNDED
        assert [r.amount.amount for r in _refund_rows(order)] == [
            Decimal("30.00"),
            Decimal("25.00"),
            Decimal("45.00"),
        ]
        assert [r.description for r in _refund_rows(order)] == [
            "one item back",
            "another item back",
            "the rest",
        ]
        # What reached Stripe, in minor units, one idempotency key per
        # refund numbered under the order lock.
        assert stripe_api.amounts == [3000, 2500, 4500]
        assert stripe_api.keys == [
            f"order-{order.pk}-refund-1",
            f"order-{order.pk}-refund-2",
            f"order-{order.pk}-refund-3",
        ]
        assert {c["payment_intent"] for c in stripe_api.calls} == {"pi_ledger"}
        # ``order_refunded`` says what each refund paid back.
        assert [r["amount"].amount for r in refunded_signals] == [
            Decimal("30.00"),
            Decimal("25.00"),
            Decimal("45.00"),
        ]

    def test_the_caller_sees_the_derived_status_on_its_own_object(self):
        order = _order("100.00")

        with FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("30.00"))

        assert order.payment_status == PaymentStatus.PARTIALLY_REFUNDED

    def test_the_refunds_report_which_ids_they_made(self):
        order = _order("100.00")

        with FakeStripeRefunds():
            _success, response = OrderService.refund_order(
                order, amount=_eur("30.00")
            )

        assert response["refund_id"] == "re_1"
        assert response["refund_ids"] == ["re_1"]
        assert Decimal(response["amount"]) == Decimal("30.00")


class TestNeverMoreThanIsLeft:
    def test_a_refund_above_the_remainder_is_refused_before_stripe(self):
        order = _order("100.00")
        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order, amount=_eur("30.00"))
            stripe_api.calls.clear()

            with pytest.raises(PaymentError) as excinfo:
                OrderService.refund_order(order, amount=_eur("70.01"))

        assert str(excinfo.value) == (
            "Refund amount (70.01) cannot exceed the refundable amount (70.00)."
        )
        assert stripe_api.calls == []
        assert _refund_rows(order).count() == 1

    def test_the_exact_remainder_is_refundable(self):
        order = _order("100.00")
        with FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("30.00"))
            success, _ = OrderService.refund_order(order, amount=_eur("70.00"))

        assert success is True
        assert _status(order) == PaymentStatus.REFUNDED

    def test_nothing_more_can_be_refunded_once_it_is_all_back(self):
        order = _order("100.00")
        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order)
            stripe_api.calls.clear()

            with pytest.raises(PaymentError) as excinfo:
                OrderService.refund_order(order, amount=_eur("0.01"))

        assert str(excinfo.value) == "This order has already been refunded."
        assert stripe_api.calls == []

    def test_a_partially_refunded_order_is_no_longer_refused_outright(self):
        """It used to raise "already refunded" for any refund at all."""
        order = _order("100.00")
        with FakeStripeRefunds():
            OrderService.refund_order(order, amount=_eur("10.00"))
            assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED

            success, _ = OrderService.refund_order(order, amount=_eur("10.00"))

        assert success is True


class TestIdempotency:
    def test_a_retry_after_an_unrecorded_refund_is_not_a_second_refund(self):
        """The refund was made at Stripe but its row never committed (the
        request died, the transaction rolled back): asking again is a retry
        under the SAME key, and Stripe answers with the refund it already
        made instead of making another."""
        order = _order("100.00")
        real_record = PaymentLedger.record_refund

        with FakeStripeRefunds() as stripe_api:
            with mock.patch.object(
                PaymentLedger, "record_refund", side_effect=RuntimeError("db")
            ):
                with pytest.raises(RuntimeError):
                    OrderService.refund_order(order, amount=_eur("40.00"))
            assert not _refund_rows(order).exists()
            assert len(stripe_api.created) == 1

            with mock.patch.object(
                PaymentLedger, "record_refund", side_effect=real_record
            ):
                success, response = OrderService.refund_order(
                    order, amount=_eur("40.00")
                )

        assert success is True
        assert stripe_api.keys == [f"order-{order.pk}-refund-1"] * 2
        # Two requests, ONE refund at Stripe, one row in the ledger.
        assert len(stripe_api.created) == 1
        assert response["refund_id"] == "re_1"
        assert [r.provider_reference for r in _refund_rows(order)] == ["re_1"]
        assert PaymentLedger.refundable(order) == Decimal("60.00")

    def test_a_key_reused_with_another_amount_is_stripes_error_not_a_refund(
        self,
    ):
        """After an attempt whose outcome is unknown, asking for a
        DIFFERENT amount under the same key is refused by Stripe (it cannot
        tell it from the first); nothing is booked."""
        order = _order("100.00")

        with FakeStripeRefunds() as stripe_api:
            with mock.patch.object(
                PaymentLedger, "record_refund", side_effect=RuntimeError("db")
            ):
                with pytest.raises(RuntimeError):
                    OrderService.refund_order(order, amount=_eur("40.00"))

            success, response = OrderService.refund_order(
                order, amount=_eur("25.00")
            )

        assert success is False
        assert response["stripe_error"] is True
        assert len(stripe_api.created) == 1
        assert not _refund_rows(order).exists()
        assert _status(order) == PaymentStatus.COMPLETED

    def test_a_recorded_refund_moves_the_next_one_to_a_new_key(self):
        order = _order("100.00")

        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order, amount=_eur("10.00"))
            OrderService.refund_order(order, amount=_eur("10.00"))

        assert len(set(stripe_api.keys)) == 2
        assert len(stripe_api.created) == 2

    def test_the_order_is_locked_before_the_ledger_is_read(self):
        """The number in the key and the remainder are read under the lock:
        the ``FOR UPDATE`` on the order comes before any ledger query."""
        order = _order("100.00")

        with FakeStripeRefunds(), CaptureQueriesContext(connection) as queries:
            OrderService.refund_order(order, amount=_eur("10.00"))

        statements = [q["sql"] for q in queries.captured_queries]
        locked = next(
            i
            for i, sql in enumerate(statements)
            if "FOR UPDATE" in sql and '"order_order"' in sql
        )
        first_ledger_read = next(
            i
            for i, sql in enumerate(statements)
            if 'FROM "order_paymenttransaction"' in sql
        )
        assert locked < first_ledger_read


class TestAStripeRefundThatIsNotSettled:
    """A refund Stripe took on but has not finished is booked as refunded;
    one that waits on the customer, failed or was canceled is not. Neither
    leaves the order in PROCESSING, a status the success event that arrives
    late would write COMPLETED over."""

    def test_a_pending_refund_is_booked_in_a_settled_status(
        self, refunded_signals
    ):
        order = _order("100.00")

        with FakeStripeRefunds(status="pending"):
            success, response = OrderService.refund_order(order)

        assert success is True
        assert response["stripe_status"] == "pending"
        assert _status(order) == PaymentStatus.REFUNDED
        assert len(refunded_signals) == 1

    def test_a_late_payment_succeeded_cannot_rewrite_a_pending_refund(self):
        order = _order("100.00")
        with FakeStripeRefunds(status="pending"):
            OrderService.refund_order(order, amount=_eur("40.00"))

        outcome = OrderService.handle_payment_succeeded(
            "pi_ledger", captured=CAPTURED
        )

        assert outcome is not None
        assert outcome.applied is False
        assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED

    @pytest.mark.parametrize(
        "status", ["requires_action", "failed", "canceled"]
    )
    def test_a_refund_stripe_did_not_take_on_records_nothing(
        self, status, refunded_signals
    ):
        order = _order("100.00")

        with FakeStripeRefunds(status=status):
            success, response = OrderService.refund_order(order)

        assert success is False
        assert response["stripe_status"] == status
        assert not _refund_rows(order).exists()
        # Not PROCESSING: nothing was refunded, the payment still reads paid.
        assert _status(order) == PaymentStatus.COMPLETED
        assert refunded_signals == []

    def test_staff_find_why_in_the_order_history(self):
        order = _order("100.00")

        with FakeStripeRefunds(status="requires_action"):
            OrderService.refund_order(order)

        notes = [
            row.new_value["note"]
            for row in OrderHistory.objects.filter(
                order=order, change_type="NOTE"
            )
            if row.new_value["note"].startswith("Refund via")
        ]
        assert notes == [
            (
                "Refund via stripe was not processed: Stripe refund re_1 is "
                "requires_action, not completed."
            )
        ]

    def test_a_stripe_error_records_nothing(self, refunded_signals):
        order = _order("100.00")

        with FakeStripeRefunds(fail_with=stripe.APIError("down")):
            success, response = OrderService.refund_order(order)

        assert success is False
        assert response["stripe_error"] is True
        assert not _refund_rows(order).exists()
        assert refunded_signals == []


class TestAnOrderPaidTwice:
    def _paid_twice(self):
        order = _order("60.00")
        PaymentTransactionFactory(
            order=order, amount=_eur("40.00"), provider_reference="pi_second"
        )
        return order

    def test_everything_is_refunded_payment_by_payment(self, refunded_signals):
        order = self._paid_twice()

        with FakeStripeRefunds() as stripe_api:
            success, response = OrderService.refund_order(order)

        assert success is True
        assert [
            (c["payment_intent"], c["amount"]) for c in stripe_api.calls
        ] == [("pi_ledger", 6000), ("pi_second", 4000)]
        assert stripe_api.keys == [
            f"order-{order.pk}-refund-1",
            f"order-{order.pk}-refund-2",
        ]
        assert _status(order) == PaymentStatus.REFUNDED
        assert response["refund_ids"] == ["re_1", "re_2"]
        # One signal per refund the ledger took.
        assert [r["amount"].amount for r in refunded_signals] == [
            Decimal("60.00"),
            Decimal("40.00"),
        ]

    def test_a_refund_larger_than_one_payment_is_split(self):
        order = self._paid_twice()

        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order, amount=_eur("75.00"))

        assert [
            (c["payment_intent"], c["amount"]) for c in stripe_api.calls
        ] == [("pi_ledger", 6000), ("pi_second", 1500)]
        assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED
        assert PaymentLedger.refundable(order) == Decimal("25.00")

    def test_a_payment_that_fails_midway_keeps_what_was_already_refunded(
        self, refunded_signals
    ):
        order = self._paid_twice()
        calls = {"n": 0}

        class _Stripe(FakeStripeRefunds):
            def _create(self, **kwargs):
                calls["n"] += 1
                if calls["n"] == 2:
                    raise stripe.APIError("second payment refused")
                return super()._create(**kwargs)

        with _Stripe():
            success, response = OrderService.refund_order(order)

        assert success is False
        assert response["stripe_error"] is True
        # The first payment's refund was made and is on the ledger.
        assert [r.provider_reference for r in _refund_rows(order)] == ["re_1"]
        assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED
        assert [r["amount"].amount for r in refunded_signals] == [
            Decimal("60.00")
        ]


class TestACaptureTheLedgerNeverRecorded:
    def _unrecorded(self):
        return OrderFactory(
            num_order_items=0,
            pay_way=PayWayFactory.create_online_payment(),
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="pi_unrecorded",
            paid_amount=_eur("50.00"),
        )

    def test_it_is_read_from_stripe_once_and_recorded(self):
        order = self._unrecorded()
        intent = SimpleNamespace(
            status="succeeded", amount=5000, currency="eur", created=1
        )

        with (
            FakeStripeRefunds() as stripe_api,
            mock.patch(
                "order.payment.stripe.PaymentIntent.retrieve",
                return_value=intent,
            ) as retrieve,
            mock.patch("order.payment.PaymentIntent.sync_from_stripe_data"),
        ):
            first, _ = OrderService.refund_order(order, amount=_eur("20.00"))
            second, _ = OrderService.refund_order(order, amount=_eur("30.00"))

        assert (first, second) == (True, True)
        # Read once; from then on the ledger answers.
        assert retrieve.call_count == 1
        [charge] = order.payment_transactions.filter(
            kind=PaymentTransactionKind.CHARGE
        )
        assert charge.provider_reference == "pi_unrecorded"
        assert charge.amount == _eur("50.00")
        assert stripe_api.amounts == [2000, 3000]
        assert _status(order) == PaymentStatus.REFUNDED

    def test_a_payment_stripe_cannot_confirm_is_not_refunded(self):
        order = self._unrecorded()
        intent = SimpleNamespace(
            status="requires_payment_method",
            amount=5000,
            currency="eur",
            created=1,
        )

        with (
            FakeStripeRefunds() as stripe_api,
            mock.patch(
                "order.payment.stripe.PaymentIntent.retrieve",
                return_value=intent,
            ),
            mock.patch("order.payment.PaymentIntent.sync_from_stripe_data"),
        ):
            success, response = OrderService.refund_order(order)

        assert success is False
        assert "captured" in response["error"]
        assert stripe_api.calls == []
        assert not order.payment_transactions.exists()
