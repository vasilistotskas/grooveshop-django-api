"""Credits are issued from the allocation stored when the money was claimed.

Every refund (a claimed attempt, a webhook-booked row, a returned
chargeback) fixes its per-rate share of the receipt under the order lock;
the credit document is issued from that share only, so the order credits are
issued in cannot change it. See ``order.credit_allocation``.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
import stripe
from django.apps import apps

from order import credit_allocation
from order.credit_allocation_backfill import backfill_credit_allocations
from order.credit_documents import create_pending_credits
from order.enum.payment_transaction import PaymentTransactionKind
from order.exceptions import PaymentError
from order.models import CreditAllocation, RetailCredit
from order.models.refund_attempt import RefundAttemptStatus
from order.payment_ledger import PaymentLedger
from order.services import OrderService
from tests.integration.order.test_mydata_lifecycle import (
    _credits,
    _eur,
    _order,
    _receipt,
)
from tests.utils.mydata import FakeAade, mydata_enabled
from tests.utils.stripe_refunds import FakeStripeRefunds

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


@pytest.fixture(autouse=True)
def _render_and_site(settings):
    settings.SITE_NAME = "GrooveShop"
    settings.INFO_EMAIL = "support@example.com"
    settings.NUXT_BASE_URL = "http://example.com"
    settings.STATIC_BASE_URL = "http://example.com"
    settings.DEFAULT_FROM_EMAIL = "no-reply@example.com"
    with patch(
        "order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test"
    ):
        yield


def _gross(credit) -> dict[Decimal, Decimal]:
    return {
        Decimal(r["rate"]): Decimal(r["gross"]) for r in credit.vat_breakdown
    }


def _mixed():
    return _order(lines=(("24", "60.00"), ("13", "40.00")))


class TestTheSplitIsFixedWhenClaimed:
    def test_a_claim_stores_its_allocation_under_the_headroom_left(self):
        order = _mixed()

        with mydata_enabled(), FakeAade(), FakeStripeRefunds():
            _receipt(order)
            OrderService.refund_order(order, amount=_eur("30.00"))
            OrderService.refund_order(order, amount=_eur("20.00"))

        first, second = order.refund_attempts.order_by("id")
        assert credit_allocation.allocation_of(refund_attempt=first) == {
            Decimal(24): Decimal("18.00"),
            Decimal(13): Decimal("12.00"),
        }
        # Pro rata over what was left after the first, to the cent.
        assert credit_allocation.allocation_of(refund_attempt=second) == {
            Decimal(24): Decimal("12.00"),
            Decimal(13): Decimal("8.00"),
        }
        assert credit_allocation.headroom(order) == {
            Decimal(24): Decimal("30.00"),
            Decimal(13): Decimal("20.00"),
        }

    def test_a_refund_the_receipt_cannot_hold_is_refused_before_money_moves(
        self,
    ):
        order = _order(lines=(("24", "100.00"),))
        from order.models.payment_transaction import PaymentTransaction

        with mydata_enabled(), FakeAade(), FakeStripeRefunds() as api:
            _receipt(order)
            # The ledger holds 100.00 more than the receipt (a second
            # payment): the receipt, not the ledger, bounds the credit.
            PaymentTransaction.objects.create(
                order=order,
                kind=PaymentTransactionKind.CHARGE,
                provider="stripe",
                provider_reference="pi_extra",
                amount=_eur("50.00"),
            )
            with pytest.raises(PaymentError, match="holds only"):
                OrderService.refund_order(order, amount=_eur("150.00"))

        assert api.calls == []
        assert not order.refund_attempts.exists()


class TestOrderIndependence:
    """The same refunds settled in any order issue the same credits."""

    @pytest.mark.parametrize("settled", [["A", "B"], ["B", "A"]])
    def test_the_credits_do_not_depend_on_the_settlement_order(self, settled):
        order = _mixed()
        amounts = {"A": "30.00", "B": "20.00"}

        with mydata_enabled(), FakeAade():
            _receipt(order)
            charge = order.payment_transactions.get(
                kind=PaymentTransactionKind.CHARGE
            )
            # Both refunds are claimed (in flight) before either settles.
            with FakeStripeRefunds(fail_with=stripe.APIConnectionError("x")):
                OrderService.refund_order(order, amount=_eur(amounts["A"]))
                OrderService.refund_order(order, amount=_eur(amounts["B"]))
            for name in settled:
                PaymentLedger.record_refund(
                    order,
                    charge=charge,
                    amount=_eur(amounts[name]),
                    reference=f"re_{name}",
                )
                create_pending_credits(order.id)

        assert {c.total.amount: _gross(c) for c in _credits(order)} == {
            Decimal("30.00"): {
                Decimal(24): Decimal("18.00"),
                Decimal(13): Decimal("12.00"),
            },
            Decimal("20.00"): {
                Decimal(24): Decimal("12.00"),
                Decimal(13): Decimal("8.00"),
            },
        }


class TestAClaimBeforeTheReceiptBookedAfter:
    def test_it_keeps_its_room_and_is_credited_from_its_allocation(self):
        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )

        with mydata_enabled(), FakeAade():
            with FakeStripeRefunds(fail_with=stripe.APIConnectionError("x")):
                OrderService.refund_order(order, amount=_eur("30.00"))
            [attempt] = order.refund_attempts.all()
            # Allocated against the order's lines: no receipt yet.
            assert credit_allocation.allocation_of(refund_attempt=attempt) == {
                Decimal(24): Decimal("18.00"),
                Decimal(13): Decimal("12.00"),
            }
            invoice = _receipt(order)
            # The receipt is not net of money still in flight.
            assert invoice.total.amount == Decimal("100.00")
            assert credit_allocation.headroom(order) == {
                Decimal("24.0"): Decimal("42.00"),
                Decimal("13.0"): Decimal("28.00"),
            }
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_1"
            )
            create_pending_credits(order.id)

        [credit] = _credits(order)
        assert _gross(credit) == {
            Decimal(24): Decimal("18.00"),
            Decimal(13): Decimal("12.00"),
        }


class TestAReversalFreesTheAllocation:
    def _booked(self, *, auto_submit: bool):
        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(auto_submit=auto_submit), FakeAade():
            _receipt(order)
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_1"
            )
            if auto_submit:
                create_pending_credits(order.id)
        return order, charge

    def test_a_failed_refund_gives_its_room_back_unless_credited(self):
        order, charge = self._booked(auto_submit=False)
        assert credit_allocation.headroom_total(order) == Decimal("70.00")

        PaymentLedger.record_refund_reversal(
            order, charge=charge, amount=_eur("10.00"), reference="re_1"
        )

        # Scaled by what was reversed.
        assert credit_allocation.headroom_total(order) == Decimal("80.00")

    def test_a_refund_reversed_in_full_is_not_allocated_again(self):
        order, charge = self._booked(auto_submit=False)

        PaymentLedger.record_refund_reversal(
            order, charge=charge, amount=_eur("30.00"), reference="re_1"
        )
        credit_allocation.ensure_allocations(order)

        assert credit_allocation.headroom_total(order) == Decimal("100.00")

    def test_a_refund_that_was_credited_keeps_its_room(self):
        order, charge = self._booked(auto_submit=True)
        assert _credits(order).count() == 1

        PaymentLedger.record_refund_reversal(
            order, charge=charge, amount=_eur("30.00"), reference="re_1"
        )

        # The credit document stays: it records what the merchant owes.
        assert credit_allocation.headroom_total(order) == Decimal("70.00")


class TestUnclaimedMoneyIsAllocatedWhenBooked:
    def test_a_webhook_refund_gets_pro_rata_over_the_headroom_then(self):
        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )

        with mydata_enabled(), FakeAade():
            _receipt(order)
            row, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("50.00"), reference="re_x"
            )
            create_pending_credits(order.id)

        assert credit_allocation.allocation_of(refund=row) == {
            Decimal(24): Decimal("30.00"),
            Decimal(13): Decimal("20.00"),
        }
        assert Decimal(_credits(order).get().total.amount) == Decimal("50.00")


class TestTheBackfill:
    def test_issued_credits_keep_matching_their_allocations(self):
        order = _mixed()
        with mydata_enabled(), FakeAade(), FakeStripeRefunds():
            _receipt(order)
            OrderService.refund_order(order, amount=_eur("30.00"))
            OrderService.refund_order(order, amount=_eur("20.00"))
        before = credit_allocation.headroom(order)
        CreditAllocation.objects.all().delete()

        done = backfill_credit_allocations(apps)

        assert done == 2
        assert credit_allocation.headroom(order) == before
        for credit in RetailCredit.objects.filter(order=order):
            attempt = credit.refund.attempt
            assert credit_allocation.allocation_of(refund_attempt=attempt) == {
                Decimal(r["rate"]): Decimal(r["gross"])
                for r in credit.vat_breakdown
            }
        assert backfill_credit_allocations(apps) == 0

    def test_a_source_with_a_credit_is_never_allocated_twice(self):
        order = _mixed()
        with mydata_enabled(), FakeAade(), FakeStripeRefunds():
            _receipt(order)
            OrderService.refund_order(order, amount=_eur("30.00"))
        CreditAllocation.objects.all().delete()

        credit_allocation.ensure_allocations(order)

        assert not CreditAllocation.objects.exists()
        assert credit_allocation.headroom_total(order) == Decimal("70.00")


class TestAnAttemptStatesItsStatus:
    def test_a_declined_attempt_holds_no_room(self):
        order = _mixed()
        with mydata_enabled(), FakeAade():
            _receipt(order)
            with FakeStripeRefunds(
                fail_with=stripe.InvalidRequestError("no", "x")
            ):
                OrderService.refund_order(order, amount=_eur("30.00"))
        [attempt] = order.refund_attempts.all()
        assert attempt.status == RefundAttemptStatus.DECLINED
        assert credit_allocation.headroom_total(order) == Decimal("100.00")
