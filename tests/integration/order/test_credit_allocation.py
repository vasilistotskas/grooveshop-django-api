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

from giftcard.factories import GiftCardFactory
from giftcard.models import GiftCardTransaction, GiftCardTransactionKind
from loyalty.enum import TransactionType
from loyalty.models.transaction import PointsTransaction
from order import credit_allocation
from order.credit_allocation_backfill import backfill_credit_allocations
from order.credit_documents import create_pending_credits
from order.enum.payment_transaction import PaymentTransactionKind
from order.exceptions import PaymentError
from order.models import (
    CreditAllocation,
    Order,
    OrderHistory,
    OrderItem,
)
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
        assert backfill_credit_allocations(apps) == 0

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


def _rows(**source) -> list[tuple]:
    return sorted(
        (r.rate, r.amount.amount, r.moved_at)
        for r in CreditAllocation.objects.filter(**source)
    )


class TestTheBackfill:
    """Money booked before allocations existed is allocated by the
    migration to exactly what booking it would have allocated."""

    def test_a_booked_refund_gets_what_booking_it_gave(self):
        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade():
            _receipt(order)
            first, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_1"
            )
            second, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("20.00"), reference="re_2"
            )
        booked = (_rows(refund=first), _rows(refund=second))
        CreditAllocation.objects.all().delete()

        assert backfill_credit_allocations(apps) == 2

        assert (_rows(refund=first), _rows(refund=second)) == booked
        assert backfill_credit_allocations(apps) == 0

    def test_a_refund_booked_before_the_receipt_nets_it(self):
        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        row, _ = PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("30.00"), reference="re_1"
        )
        booked = _rows(refund=row)
        CreditAllocation.objects.all().delete()

        backfill_credit_allocations(apps)

        assert _rows(refund=row) == booked
        with mydata_enabled(), FakeAade():
            invoice = _receipt(order)
        assert invoice.total.amount == Decimal("70.00")

    def test_a_credited_refund_is_never_allocated(self):
        order = _mixed()
        with mydata_enabled(), FakeAade(), FakeStripeRefunds():
            _receipt(order)
            OrderService.refund_order(order, amount=_eur("30.00"))
            create_pending_credits(order.id)
        CreditAllocation.objects.all().delete()

        assert backfill_credit_allocations(apps) == 0

        assert not CreditAllocation.objects.exists()
        # The document itself holds the room.
        assert credit_allocation.headroom_total(order) == Decimal("70.00")

    def test_a_chargeback_decision_gets_what_deciding_it_gave(self):
        from order.chargebacks import decide_chargebacks
        from tests.integration.order.test_chargeback_credit import (
            _chargeback,
        )
        from user.factories import UserAccountFactory

        order = _mixed()
        staff = UserAccountFactory(is_staff=True, is_superuser=True)
        with mydata_enabled(auto_submit=False), FakeAade():
            _receipt(order)
            _chargeback(order, "40.00")
            [decision] = decide_chargebacks(
                order.id, goods_returned=True, user=staff
            )
        decided = _rows(decision=decision)
        assert decided
        CreditAllocation.objects.all().delete()

        assert backfill_credit_allocations(apps) == 1

        assert _rows(decision=decision) == decided

    def test_a_pending_provider_attempt_holds_its_room(self):
        order = _mixed()
        with mydata_enabled(), FakeAade():
            _receipt(order)
            with FakeStripeRefunds(fail_with=stripe.APIConnectionError("x")):
                OrderService.refund_order(order, amount=_eur("30.00"))
        [attempt] = order.refund_attempts.all()
        claimed = _rows(refund_attempt=attempt)
        CreditAllocation.objects.all().delete()

        assert backfill_credit_allocations(apps) == 1

        assert _rows(refund_attempt=attempt) == claimed

    def test_a_refund_reversed_in_part_is_allocated_net(self):
        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(auto_submit=False), FakeAade():
            _receipt(order)
            row, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_1"
            )
            PaymentLedger.record_refund_reversal(
                order, charge=charge, amount=_eur("10.00"), reference="re_1"
            )
        scaled = _rows(refund=row)
        CreditAllocation.objects.all().delete()

        backfill_credit_allocations(apps)

        assert _rows(refund=row) == scaled
        assert sum(r[1] for r in scaled) == Decimal("20.00")


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


def _points_order(*, gift: str = "0.00"):
    """Goods of 100.00 at 24%, of which 10.00 was paid with 1000 loyalty
    points (a discount: the receipt holds only what was paid), *gift* by a
    gift card, the rest by card."""
    from user.factories.account import UserAccountFactory

    user = UserAccountFactory(num_addresses=0)
    card = Decimal("90.00") - Decimal(gift)
    order = _order(lines=(("24", "100.00"),), user=user)
    OrderItem.objects.filter(order=order).update(
        discount_allocation=_eur("10.00")
    )
    Order.objects.filter(pk=order.pk).update(
        loyalty_discount=_eur("10.00"),
        gift_card_amount=_eur(gift),
        amount_due=_eur(str(card)),
    )
    order.payment_transactions.update(amount=_eur(str(card)))
    PointsTransaction.objects.create(
        user=user, points=1000, transaction_type=TransactionType.EARN
    )
    PointsTransaction.objects.create(
        user=user,
        points=-1000,
        transaction_type=TransactionType.REDEEM,
        reference_order=order,
    )
    if Decimal(gift) > 0:
        GiftCardTransaction.objects.create(
            gift_card=GiftCardFactory(initial_value=_eur(gift)),
            kind=GiftCardTransactionKind.REDEEM,
            amount=-Decimal(gift),
            order=order,
        )
    order.refresh_from_db()
    return order


class TestPointsOweNoCredit:
    """Points are a discount: the receipt does not hold their value, so a
    refund's points share neither takes room nor owes a credit."""

    def test_a_full_refund_of_an_order_paid_partly_with_points_succeeds(self):
        order = _points_order()

        with mydata_enabled(), FakeAade(), FakeStripeRefunds():
            invoice = _receipt(order)
            assert invoice.total.amount == Decimal("90.00")
            result = OrderService.refund_order(order)
            create_pending_credits(order.id)

        # The card is paid back and the points are restored.
        assert result.complete
        assert result.refunded.amount == Decimal("90.00")
        assert result.stored.amount == Decimal("10.00")
        provider, stored = order.refund_attempts.order_by("id")
        assert credit_allocation.allocation_of(refund_attempt=provider) == {
            Decimal(24): Decimal("90.00")
        }
        assert credit_allocation.allocation_of(refund_attempt=stored) == {}
        [credit] = _credits(order)
        assert credit.total.amount == Decimal("90.00")
        assert credit_allocation.headroom_total(order) == Decimal("0.00")

    def test_a_gift_card_is_payment_and_stays_in_the_allocation(self):
        order = _points_order(gift="20.00")

        with mydata_enabled(), FakeAade(), FakeStripeRefunds():
            _receipt(order)
            result = OrderService.refund_order(order)
            create_pending_credits(order.id)

        # 70 by card + 20 gift card + 10 points = the 100 of goods.
        assert result.value_returned.amount == Decimal("100.00")
        assert sum(
            (c.total.amount for c in _credits(order)), Decimal(0)
        ) == Decimal("90.00")
        assert _credits(order).count() == 2
        assert credit_allocation.headroom_total(order) == Decimal("0.00")

    def test_points_restored_before_the_receipt_are_not_paid_back(self):
        from order.invoicing import fully_refunded_before_receipt
        from order.models.refund_attempt import (
            RefundAttempt,
            RefundAttemptKind,
        )

        order = _points_order()
        attempt = RefundAttempt.objects.create(
            order=order,
            kind=RefundAttemptKind.STORED_VALUE,
            amount=_eur("10.00"),
        )
        OrderService._return_stored_value(order, attempt, "points back")

        # 10.00 of points are back; the 90.00 that was paid is still held.
        assert not fully_refunded_before_receipt(order)


class TestCreditsAreIssuedNetOfReversals:
    def _booked(self):
        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(auto_submit=False), FakeAade():
            _receipt(order)
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_1"
            )
        return order, charge

    def test_a_partial_reversal_before_the_credit_is_credited_net(self):
        order, charge = self._booked()
        PaymentLedger.record_refund_reversal(
            order, charge=charge, amount=_eur("10.00"), reference="re_1"
        )

        with mydata_enabled(), FakeAade():
            create_pending_credits(order.id)

        [credit] = _credits(order)
        assert credit.total.amount == Decimal("20.00")
        assert _gross(credit) == {
            Decimal(24): Decimal("12.00"),
            Decimal(13): Decimal("8.00"),
        }

    def test_a_full_reversal_before_the_credit_issues_none(self):
        order, charge = self._booked()
        PaymentLedger.record_refund_reversal(
            order, charge=charge, amount=_eur("30.00"), reference="re_1"
        )

        with mydata_enabled(), FakeAade():
            create_pending_credits(order.id)
            assert not _credits(order).exists()
            assert not CreditAllocation.objects.exists()
            assert credit_allocation.headroom_total(order) == Decimal("100.00")
            # The room is real: a later refund is credited in full.
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("100.00"), reference="re_2"
            )
            create_pending_credits(order.id)

        [credit] = _credits(order)
        assert credit.total.amount == Decimal("100.00")

    def test_a_reversal_after_the_credit_stands_and_tells_the_accountant(self):
        order, charge = self._booked()
        with mydata_enabled(), FakeAade():
            create_pending_credits(order.id)
        [credit] = _credits(order)

        PaymentLedger.record_refund_reversal(
            order, charge=charge, amount=_eur("30.00"), reference="re_1"
        )

        credit.refresh_from_db()
        assert credit.total.amount == Decimal("30.00")
        assert credit_allocation.headroom_total(order) == Decimal("70.00")
        note = OrderHistory.objects.filter(order=order, change_type="NOTE")
        assert "re_1 failed" in note.latest("id").new_value["note"]
        assert list(credit_allocation.reversed_credits()) == [credit]
        from admin.dashboard.store import widgets
        from tests.unit.admin.dashboard.test_store_widgets import (
            _context,
            _superuser,
        )

        titles = [
            str(alert["title"])
            for alert in _context(widgets.StoreAlerts, _superuser())["alerts"]
        ]
        assert "A refunded credit document was reversed" in titles


class TestReadPathsNeverWrite:
    def test_a_refund_without_an_allocation_is_reported_not_allocated(self):
        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade():
            _receipt(order)
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_1"
            )
            CreditAllocation.objects.all().delete()
            create_pending_credits(order.id)

        assert not _credits(order).exists()
        assert not CreditAllocation.objects.exists()
        assert OrderHistory.objects.filter(
            order=order, new_value__note__icontains="No credit document"
        ).exists()

    def test_the_receipt_render_writes_no_allocation(self):
        from order.invoicing import generate_invoice

        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("30.00"), reference="re_1"
        )
        CreditAllocation.objects.all().delete()
        Order.objects.filter(pk=order.pk).update(status="SHIPPED")

        with mydata_enabled(), FakeAade():
            generate_invoice(Order.objects.get(pk=order.pk))

        assert not CreditAllocation.objects.exists()


class TestTheHeadroomCountsTheCreditsThemselves:
    def test_an_issued_credit_counts_by_its_own_rows_not_its_sources(self):
        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade():
            _receipt(order)
            row, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_1"
            )
            create_pending_credits(order.id)
        assert credit_allocation.headroom_total(order) == Decimal("70.00")

        # Whatever the source's allocation says, the document is what was
        # sent to AADE: the headroom reads it.
        CreditAllocation.objects.filter(refund=row).update(amount=_eur("1.00"))

        assert credit_allocation.headroom(order) == {
            Decimal("24.0"): Decimal("42.00"),
            Decimal("13.0"): Decimal("28.00"),
        }


class TestALinkedBookedRefundKeepsOneAllocation:
    def test_linking_an_attempt_drops_the_allocation_of_its_booked_row(self):
        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade():
            _receipt(order)
            with FakeStripeRefunds(fail_with=stripe.APIConnectionError("x")):
                OrderService.refund_order(order, amount=_eur("30.00"))
            [attempt] = order.refund_attempts.all()
            # The provider's webhook books 29.00 (not the claimed 30.00):
            # it matches no attempt and is allocated as unclaimed money.
            row, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("29.00"), reference="re_w"
            )
            assert credit_allocation.allocation_of(refund=row)
            # The answer to the call arrives: the same refund id.
            PaymentLedger.record_refund(
                order,
                charge=charge,
                amount=_eur("29.00"),
                reference="re_w",
                attempt=attempt,
            )

        assert credit_allocation.allocation_of(refund=row) == {}
        assert sum(
            credit_allocation.allocation_of(refund_attempt=attempt).values(),
            Decimal(0),
        ) == Decimal("29.00")
        assert credit_allocation.headroom_total(order) == Decimal("71.00")


class TestSmallCentsDoNotDependOnTheSettlementOrder:
    """The order dependence that really existed: a credit was spread over
    the rates when it was issued, so cents rounded differently for the same
    two refunds settled in the other order."""

    @pytest.mark.parametrize("settled", [["A", "B"], ["B", "A"]])
    def test_the_same_refunds_issue_the_same_credits(self, settled):
        order = _order(lines=(("24", "0.50"), ("13", "0.50")))
        amounts = {"A": "0.01", "B": "0.99"}

        with mydata_enabled(), FakeAade():
            _receipt(order)
            charge = order.payment_transactions.get(
                kind=PaymentTransactionKind.CHARGE
            )
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

        by_total = {c.total.amount: _gross(c) for c in _credits(order)}
        assert by_total == {
            # Claimed first, A took the cent where the headroom rounds it.
            Decimal("0.01"): {Decimal(13): Decimal("0.01")},
            Decimal("0.99"): {
                Decimal(24): Decimal("0.50"),
                Decimal(13): Decimal("0.49"),
            },
        }
