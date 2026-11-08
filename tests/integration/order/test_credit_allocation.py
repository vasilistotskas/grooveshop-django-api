"""Credits are issued from the allocation stored when the money was claimed.

Every refund (a claimed attempt, a webhook-booked row, a returned
chargeback) fixes its per-rate share of the receipt under the order lock;
the credit document is issued from that share only, so the order credits are
issued in cannot change it. See ``order.credit_allocation``.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
import stripe

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
    Invoice,
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
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )

        with mydata_enabled(), FakeAade(), FakeStripeRefunds() as api:
            _receipt(order)
            # A credit that took 60.00 of the receipt (booked outside any
            # claim): the receipt, not the ledger, bounds the next credit.
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("60.00"), reference="re_60"
            )
            # And 100.00 paid twice: 40.00 of the 140.00 held is the
            # duplicate, the other 100.00 is the sale, of which the receipt
            # has 40.00 left.
            _duplicate_payment(order)
            with pytest.raises(PaymentError, match="holds only"):
                OrderService.refund_order(order, amount=_eur("140.00"))

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
        assert backfill_credit_allocations() == 0

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

        assert backfill_credit_allocations() == 2

        assert (_rows(refund=first), _rows(refund=second)) == booked
        assert backfill_credit_allocations() == 0

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

        backfill_credit_allocations()

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

        assert backfill_credit_allocations() == 0

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

        assert backfill_credit_allocations() == 1

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

        assert backfill_credit_allocations() == 1

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

        backfill_credit_allocations()

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
            # Decided, and owing nothing: one row of amount zero.
            [zero] = CreditAllocation.objects.all()
            assert zero.amount.amount == 0
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


class TestEveryWritePathAllocatesFirst:
    """What nobody allocated (a release that did not know about allocations
    booked it) is decided before a write path reads or takes the room."""

    def test_the_credit_documents_allocate_what_is_missing_then_issue(self):
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

        assert _gross(_credits(order).get()) == {
            Decimal(24): Decimal("18.00"),
            Decimal(13): Decimal("12.00"),
        }

    def test_a_receipt_issued_before_the_first_task_run_is_net(self):
        from order.invoicing import generate_invoice

        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("30.00"), reference="re_1"
        )
        # What the previous release booked: the row, no allocation.
        CreditAllocation.objects.all().delete()
        Order.objects.filter(pk=order.pk).update(status="SHIPPED")

        with mydata_enabled(), FakeAade():
            invoice = generate_invoice(Order.objects.get(pk=order.pk))

        assert invoice.total.amount == Decimal("70.00")
        assert CreditAllocation.objects.filter(refund__isnull=False).exists()

    def test_an_older_unallocated_source_takes_its_room_before_a_new_claim(
        self,
    ):
        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade(), FakeStripeRefunds() as api:
            _receipt(order)
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("60.00"), reference="re_old"
            )
            _duplicate_payment(order)
            CreditAllocation.objects.all().delete()

            # 40.00 of the 100.00 claimed is the duplicate; the other 60.00
            # fits only if the older refund had not taken its 60.00 first.
            with pytest.raises(PaymentError, match="holds only"):
                OrderService.refund_order(order, amount=_eur("100.00"))

        # Refused, and the refusal rolled the claim's step back with it.
        assert api.calls == []
        assert not order.refund_attempts.exists()


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


def _duplicate_payment(order, amount="100.00"):
    """The customer paid again: the ledger holds *amount* more than the
    order is for."""
    from order.models.payment_transaction import PaymentTransaction

    return PaymentTransaction.objects.create(
        order=order,
        kind=PaymentTransactionKind.CHARGE,
        provider="stripe",
        provider_reference="pi_duplicate",
        amount=_eur(amount),
    )


class TestADuplicatePaymentOwesNoCredit:
    """A refund pays the excess the ledger holds back first: that part gives
    a duplicate payment back, it does not reverse the sale, so it takes no
    room on the receipt and gets no credit document."""

    def test_the_duplicate_is_refundable_after_the_sale_took_its_room(self):
        order = _order(lines=(("24", "100.00"),))

        with mydata_enabled(), FakeAade(), FakeStripeRefunds():
            _receipt(order)
            _duplicate_payment(order)
            first = OrderService.refund_order(order, amount=_eur("100.00"))
            second = OrderService.refund_order(order, amount=_eur("100.00"))
            create_pending_credits(order.id)

        assert first.complete
        assert second.complete
        a_first, a_second = order.refund_attempts.order_by("id")
        # The first 100.00 gave the duplicate back: nothing owed.
        assert credit_allocation.allocation_of(refund_attempt=a_first) == {}
        assert credit_allocation.is_allocated(refund_attempt=a_first)
        assert credit_allocation.allocation_of(refund_attempt=a_second) == {
            Decimal(24): Decimal("100.00")
        }
        [credit] = _credits(order)
        assert credit.total.amount == Decimal("100.00")

    def test_a_duplicate_booked_by_a_webhook_is_not_allocated(self):
        order = _order(lines=(("24", "100.00"),))

        with mydata_enabled(), FakeAade():
            _receipt(order)
            duplicate = _duplicate_payment(order)
            row, _ = PaymentLedger.record_refund(
                order,
                charge=duplicate,
                amount=_eur("100.00"),
                reference="re_dup",
            )
            create_pending_credits(order.id)

        assert credit_allocation.allocation_of(refund=row) == {}
        assert credit_allocation.is_allocated(refund=row)
        assert not _credits(order).exists()
        assert credit_allocation.headroom_total(order) == Decimal("100.00")
        # Allocated to nothing is not "never allocated": no alarm, and the
        # backfill leaves it alone.
        assert not OrderHistory.objects.filter(
            order=order,
            new_value__note__icontains="No credit document could be issued",
        ).exists()
        assert backfill_credit_allocations() == 0


class TestASettledAttemptWithoutItsRefundHoldsNothing:
    def test_the_attempt_that_lost_the_refund_to_another_drops_its_room(self):
        from order.models.refund_attempt import RefundAttempt

        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        first, second = (
            RefundAttempt.objects.create(
                order=order, charge=charge, amount=_eur("30.00")
            )
            for _ in range(2)
        )
        OrderService._allocate_claim(order, [first])
        OrderService._allocate_claim(order, [second])
        assert credit_allocation.headroom_total(order) == Decimal("40.00")
        row, _ = PaymentLedger.record_refund(
            order,
            charge=charge,
            amount=_eur("30.00"),
            reference="re_1",
            attempt=first,
        )

        # The second overlapped run finds the same refund (nothing in the
        # provider's answer ties it to an attempt): it is settled without
        # a link, and its room goes with it.
        PaymentLedger.settle_attempt(second, row)

        second.refresh_from_db()
        assert second.status == RefundAttemptStatus.SETTLED
        assert not CreditAllocation.objects.filter(
            refund_attempt=second
        ).exists()
        assert credit_allocation.headroom_total(order) == Decimal("70.00")

    def test_the_backfill_gives_a_settled_attempt_with_no_refund_nothing(self):
        from order.models.refund_attempt import RefundAttempt

        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        RefundAttempt.objects.create(
            order=order,
            charge=charge,
            amount=_eur("30.00"),
            status=RefundAttemptStatus.SETTLED,
        )

        assert backfill_credit_allocations() == 0

        assert credit_allocation.headroom_total(order) == Decimal("100.00")


class TestSourcesBookedByTheOldReleaseAreAllocatedAfterwards:
    """The previous release keeps serving after the migration, so what it
    books has no allocation until ``allocate_credit_sources`` runs."""

    def test_the_task_allocates_them_and_the_credit_follows(self):
        from order.tasks import allocate_credit_sources

        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade():
            _receipt(order)
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_1"
            )
            # What the old release booked: the row, no allocation, and a
            # credit pass that skipped it.
            CreditAllocation.objects.all().delete()
            assert not _credits(order).exists()

            assert allocate_credit_sources() == 1
            assert allocate_credit_sources() == 0

        assert _gross(_credits(order).get()) == {
            Decimal(24): Decimal("18.00"),
            Decimal(13): Decimal("12.00"),
        }

    def test_it_is_in_the_beat_schedule_through_the_fanout(self):
        from django.conf import settings

        entry = settings.CELERY_BEAT_SCHEDULE["allocate-credit-sources"]

        assert entry["task"] == "tenant.tasks.fanout_allocate_credit_sources"


class TestTheBackfillUsesTheLiveRules:
    def test_a_credited_source_is_counted_by_its_document_only(self):
        order = _mixed()
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade():
            _receipt(order)
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_a"
            )
            create_pending_credits(order.id)
            # The old release books another 65.00 with no allocation.
            late, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("65.00"), reference="re_b"
            )
            CreditAllocation.objects.filter(refund=late).delete()

            assert backfill_credit_allocations() == 1

        # 100.00 less the 30.00 document leaves 70.00 of room: the 65.00 fits
        # whole (the credited refund's own rows are not counted twice).
        assert sum(credit_allocation.allocation_of(refund=late).values()) == (
            Decimal("65.00")
        )

    def test_a_duplicate_payment_refund_is_not_allocated(self):
        order = _order(lines=(("24", "100.00"),))
        with mydata_enabled(), FakeAade():
            _receipt(order)
            duplicate = _duplicate_payment(order)
            row, _ = PaymentLedger.record_refund(
                order,
                charge=duplicate,
                amount=_eur("100.00"),
                reference="re_dup",
            )
            CreditAllocation.objects.all().delete()

            assert backfill_credit_allocations() == 1

        assert credit_allocation.allocation_of(refund=row) == {}
        assert credit_allocation.is_allocated(refund=row)
        assert credit_allocation.headroom_total(order) == Decimal("100.00")


class TestASourceTheReceiptCannotHoldIsMarkedOnce:
    def test_it_is_allocated_to_nothing_and_noted_once(self):
        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade():
            _receipt(order)
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("100.00"), reference="re_1"
            )
            # The receipt is full: this one finds no room.
            late, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("5.00"), reference="re_2"
            )

            assert credit_allocation.is_allocated(refund=late)
            assert credit_allocation.allocation_of(refund=late) == {}
            assert backfill_credit_allocations() == 0
            create_pending_credits(order.id)
            create_pending_credits(order.id)

        notes = OrderHistory.objects.filter(
            order=order, new_value__note__icontains="receipt holds"
        )
        assert notes.count() == 1
        assert not [
            n.new_value["note"]
            for n in OrderHistory.objects.filter(
                order=order,
                new_value__note__icontains="No credit document could be issued",
            )
        ]
        assert _credits(order).count() == 1


class TestRoomHeldByAClaimInFlightIsOnlyTemporary:
    """A PENDING claim holds room that is not spent: a source booked while
    it holds the room is decided once the claim resolves, never cut to zero
    for good."""

    def test_a_refund_booked_meanwhile_is_allocated_and_credited_after(self):
        from order.tasks import allocate_credit_sources

        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade():
            _receipt(order)
            with FakeStripeRefunds(fail_with=stripe.APIConnectionError("x")):
                OrderService.refund_order(order, amount=_eur("100.00"))
            [claim] = order.refund_attempts.all()
            late, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_30"
            )

            # Not decided: no row says "owes nothing".
            assert not credit_allocation.is_allocated(refund=late)
            assert allocate_credit_sources() == 0

            claim.status = RefundAttemptStatus.DECLINED
            claim.save(update_fields=["status"])
            assert allocate_credit_sources() == 1

        assert sum(credit_allocation.allocation_of(refund=late).values()) == (
            Decimal("30.00")
        )
        [credit] = _credits(order)
        assert credit.total.amount == Decimal("30.00")

    def test_room_taken_by_settled_sources_is_final(self):
        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade():
            _receipt(order)
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("100.00"), reference="re_1"
            )
            late, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("5.00"), reference="re_2"
            )

        assert credit_allocation.is_allocated(refund=late)
        assert credit_allocation.allocation_of(refund=late) == {}


class TestTheExcessIsReadAtTheSourcesOwnMoment:
    def test_a_later_refund_does_not_give_an_older_one_more_excess(self):
        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(auto_submit=False), FakeAade():
            _receipt(order)
            duplicate = _duplicate_payment(order)
            # The duplicate is paid back, then 50.00 of the sale.
            first, _ = PaymentLedger.record_refund(
                order,
                charge=duplicate,
                amount=_eur("100.00"),
                reference="re_dup",
            )
            second, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("50.00"), reference="re_50"
            )
            CreditAllocation.objects.all().delete()

            assert backfill_credit_allocations() == 2

        assert credit_allocation.allocation_of(refund=first) == {}
        assert sum(credit_allocation.allocation_of(refund=second).values()) == (
            Decimal("50.00")
        )


def _pending_claim(order, amount):
    """A refund claim whose outcome is unknown: it stays PENDING."""
    with FakeStripeRefunds(fail_with=stripe.APIConnectionError("x")):
        OrderService.refund_order(order, amount=_eur(amount))
    return order.refund_attempts.get(amount=amount)


def _decline(claim):
    claim.status = RefundAttemptStatus.DECLINED
    claim.save(update_fields=["status"])


class TestAReceiptWaitsForUndecidedMoney:
    """A source a PENDING claim keeps undecided cannot be netted by the
    receipt, and once the receipt row exists it is never credited: the
    receipt is issued after the claim resolves."""

    def test_the_receipt_is_deferred_then_issued_net(self):
        from order.invoicing import ReceiptDeferred, generate_invoice
        from order.models import Invoice
        from order.tasks import allocate_credit_sources, generate_order_invoice
        from tests.integration.order.test_mydata_lifecycle import (
            _mark_shipped,
        )

        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade():
            claim = _pending_claim(order, "100.00")
            late, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_30"
            )
            _mark_shipped(order)

            with pytest.raises(ReceiptDeferred):
                generate_invoice(order)
            assert not Invoice.objects.filter(order=order).exists()
            assert generate_order_invoice(order.id) is False
            order.refresh_from_db()
            assert order.metadata["receipt_deferred"] is True
            # Still waiting: nothing is asked for.
            allocate_credit_sources()
            assert not Invoice.objects.filter(order=order).exists()

            _decline(claim)
            allocate_credit_sources()

        invoice = Invoice.objects.get(order=order)
        assert invoice.total.amount == Decimal("70.00")
        order.refresh_from_db()
        assert "receipt_deferred" not in order.metadata
        assert credit_allocation.is_allocated(refund=late)

    def test_a_duplicate_given_back_before_the_receipt_is_not_a_return(self):
        from order.invoicing import generate_invoice
        from tests.integration.order.test_mydata_lifecycle import (
            _mark_shipped,
        )

        order = _order(lines=(("24", "100.00"),))
        duplicate = _duplicate_payment(order)
        PaymentLedger.record_refund(
            order, charge=duplicate, amount=_eur("100.00"), reference="re_dup"
        )
        _mark_shipped(order)

        with mydata_enabled(), FakeAade():
            invoice = generate_invoice(order)

        assert invoice.total.amount == Decimal("100.00")


class TestOneRuleForUndecided:
    def test_excess_held_by_a_pending_claim_is_not_final(self):
        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade():
            _receipt(order)
            _duplicate_payment(order)
            claim = _pending_claim(order, "100.00")
            row, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("50.00"), reference="re_50"
            )

            assert not credit_allocation.is_allocated(refund=row)

            # The claim never moved money: the duplicate is still there to
            # be given back first, so the 50.00 is not the sale.
            _decline(claim)
            create_pending_credits(order.id)

        assert credit_allocation.allocation_of(refund=row) == {}
        assert not _credits(order).exists()

    def test_a_younger_source_never_takes_the_room_an_older_one_waits_for(
        self,
    ):
        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(auto_submit=False), FakeAade():
            _receipt(order)
            _pending_claim(order, "60.00")
            older, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("50.00"), reference="re_50"
            )
            younger, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("40.00"), reference="re_40"
            )

            assert not credit_allocation.is_allocated(refund=older)
            assert not credit_allocation.is_allocated(refund=younger)

            # The claim settles: the 60.00 took its room, then the older
            # refund gets what is left, and the younger finds none.
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("60.00"), reference="re_60"
            )

        assert sum(credit_allocation.allocation_of(refund=older).values()) == (
            Decimal("40.00")
        )
        assert credit_allocation.allocation_of(refund=younger) == {}

    def test_a_new_claim_is_refused_while_an_older_source_waits(self):
        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade(), FakeStripeRefunds() as api:
            _receipt(order)
            _duplicate_payment(order)
            _pending_claim(order, "60.00")
            row, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("50.00"), reference="re_50"
            )
            assert not credit_allocation.is_allocated(refund=row)
            calls = len(api.calls)

            with pytest.raises(PaymentError, match="waiting for"):
                OrderService.refund_order(order, amount=_eur("5.00"))

        assert len(api.calls) == calls

    def test_the_settlement_credit_waits_while_a_source_is_undecided(self):
        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade():
            _receipt(order)
            _pending_claim(order, "100.00")
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_30"
            )
            Order.objects.filter(pk=order.pk).update(payment_status="REFUNDED")

            create_pending_credits(order.id)

        assert not _credits(order).exists()

    def test_two_decisions_decided_in_one_pass_note_the_cap_once(self):
        from order.chargebacks import decide_chargebacks
        from order.models.payment_transaction import PaymentTransaction
        from user.factories import UserAccountFactory

        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        staff = UserAccountFactory(is_staff=True, is_superuser=True)
        with mydata_enabled(auto_submit=False), FakeAade():
            _receipt(order)
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("90.00"), reference="re_90"
            )
            for i in range(2):
                PaymentTransaction.objects.create(
                    order=order,
                    kind=PaymentTransactionKind.CHARGEBACK,
                    provider="stripe",
                    provider_reference=f"dp_{i}",
                    amount=_eur("8.00"),
                    charge=charge,
                )
            decide_chargebacks(order.id, goods_returned=True, user=staff)

        assert (
            OrderHistory.objects.filter(
                order=order, new_value__note__icontains="receipt holds"
            ).count()
            == 1
        )


class TestMoneyBeyondTheReceiptIsAnAlert:
    def test_the_dashboard_lists_the_order(self):
        from admin.dashboard.store import widgets
        from tests.unit.admin.dashboard.test_store_widgets import (
            _context,
            _superuser,
        )

        order = _order(lines=(("24", "100.00"),))
        with mydata_enabled(), FakeAade():
            _receipt(order)
            charge = order.payment_transactions.get(
                kind=PaymentTransactionKind.CHARGE
            )
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("150.00"), reference="re_150"
            )

        [uncovered] = credit_allocation.uncovered_orders()
        assert uncovered.uncovered == Decimal("50.00")
        titles = [
            str(alert["title"])
            for alert in _context(widgets.StoreAlerts, _superuser())["alerts"]
        ]
        assert "Refunded money beyond the receipt" in titles


class TestASettledClaimKeepsItsExcessWhereverItsRowFalls:
    """A claim holds the excess from when it was MADE, whether it settles
    or is declined, never from when its refund row was written."""

    def test_a_refund_waiting_for_a_claim_that_settles_owes_the_sale(self):
        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(auto_submit=False), FakeAade():
            _receipt(order)
            _duplicate_payment(order)
            # The claim takes the duplicate (a zero row), then a dashboard
            # refund of the sale is booked while it is still PENDING.
            claim = _pending_claim(order, "100.00")
            row, _ = PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("60.00"), reference="re_60"
            )
            assert not credit_allocation.is_allocated(refund=row)

            # The claim settles: its refund row is written AFTER the
            # dashboard refund, and the claim is no longer live.
            PaymentLedger.record_refund(
                order,
                charge=claim.charge,
                amount=_eur("100.00"),
                reference="re_claim",
                attempt=claim,
            )

        assert sum(credit_allocation.allocation_of(refund=row).values()) == (
            Decimal("60.00")
        )


class TestTheDeferredFlagFollowsTheReceipt:
    def test_a_receipt_that_became_unissuable_stops_being_asked_for(self):
        from order.tasks import allocate_credit_sources, generate_order_invoice
        from tests.integration.order.test_mydata_lifecycle import (
            _mark_shipped,
        )

        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(), FakeAade():
            claim = _pending_claim(order, "100.00")
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_30"
            )
            _mark_shipped(order)
            assert generate_order_invoice(order.id) is False
            order.refresh_from_db()
            assert order.metadata["receipt_deferred"] is True

            # The claim ends up refunding 70: with the 30, the whole sale.
            # There is no sale left to document.
            _decline(claim)
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("70.00"), reference="re_70"
            )
            allocate_credit_sources()

        order.refresh_from_db()
        assert not Invoice.objects.filter(order=order).exists()
        assert "receipt_deferred" not in order.metadata


class TestUncoveredFollowsWhatStands:
    @pytest.mark.parametrize(
        ("reversed_", "left"), [("30.00", "20.00"), ("100.00", None)]
    )
    def test_a_reversal_shrinks_it(self, reversed_, left):
        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(auto_submit=False), FakeAade():
            _receipt(order)
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("150.00"), reference="re_150"
            )
            [row] = credit_allocation.uncovered_orders()
            assert row.uncovered == Decimal("50.00")

            PaymentLedger.record_refund_reversal(
                order,
                charge=charge,
                amount=_eur(reversed_),
                reference="re_150",
            )

        rows = list(credit_allocation.uncovered_orders().filter(order=order))
        if left is None:
            assert rows == []
        else:
            [row] = rows
            assert row.uncovered == Decimal(left)

    def test_staff_acknowledge_it_and_the_alert_clears(self):
        from admin.dashboard.store import widgets
        from tests.unit.admin.dashboard.test_store_widgets import (
            _context,
            _superuser,
        )
        from user.factories import UserAccountFactory

        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        with mydata_enabled(auto_submit=False), FakeAade():
            _receipt(order)
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("150.00"), reference="re_150"
            )
        staff = UserAccountFactory(is_staff=True, is_superuser=True)

        assert credit_allocation.acknowledge_uncovered(order, user=staff) == 1

        assert not credit_allocation.uncovered_orders().exists()
        titles = [
            str(alert["title"])
            for alert in _context(widgets.StoreAlerts, _superuser())["alerts"]
        ]
        assert "Refunded money beyond the receipt" not in titles
        assert OrderHistory.objects.filter(
            order=order, new_value__note__icontains="acknowledged"
        ).exists()


class TestADeferredReceiptIsVisible:
    def _deferred(self):
        from order.tasks import generate_order_invoice
        from tests.integration.order.test_mydata_lifecycle import (
            _mark_shipped,
        )

        order = _order(lines=(("24", "100.00"),))
        charge = order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        )
        claim = _pending_claim(order, "100.00")
        PaymentLedger.record_refund(
            order, charge=charge, amount=_eur("30.00"), reference="re_30"
        )
        _mark_shipped(order)
        generate_order_invoice(order.id)
        return order, claim

    def test_the_dashboard_names_the_blocking_claim(self):
        from admin.dashboard.store import widgets
        from tests.unit.admin.dashboard.test_store_widgets import (
            _context,
            _superuser,
        )

        with mydata_enabled(), FakeAade():
            order, _claim = self._deferred()

        [alert] = [
            a
            for a in _context(widgets.StoreAlerts, _superuser())["alerts"]
            if str(a["title"]) == "Receipts waiting for a refund"
        ]
        assert any(
            f"#{order.pk}" in str(item) and "100" in str(item)
            for item in alert["items"]
        )

    def test_the_admin_action_says_why_instead_of_scheduling(self):
        from django.contrib.admin.sites import AdminSite

        from order.admin import OrderAdmin
        from tests.integration.order.test_admin_retail_documents import (
            _messages,
            _request,
        )
        from user.factories import UserAccountFactory

        staff = UserAccountFactory(is_staff=True, is_superuser=True)
        with mydata_enabled(), FakeAade():
            order = _order(lines=(("24", "100.00"),))
            charge = order.payment_transactions.get(
                kind=PaymentTransactionKind.CHARGE
            )
            _pending_claim(order, "100.00")
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("30.00"), reference="re_30"
            )
            from tests.integration.order.test_mydata_lifecycle import (
                _mark_shipped,
            )

            _mark_shipped(order)
            request = _request(staff)
            OrderAdmin(Order, AdminSite()).issue_receipt_now(
                request, object_id=order.pk
            )

        [message] = _messages(request)
        assert "deferred" in message
        assert "payment provider" in message
        order.refresh_from_db()
        assert order.metadata["receipt_deferred"] is True


class TestAStoredValueReturnLeftUnfinishedIsVisible:
    def test_it_is_in_an_alert_once_the_reconcile_window_has_passed(self):
        from admin.dashboard.store import widgets
        from order.models.refund_attempt import RefundAttempt, RefundAttemptKind
        from tests.unit.admin.dashboard.test_store_widgets import (
            _context,
            _superuser,
        )

        order = _order(lines=(("24", "100.00"),))
        attempt = RefundAttempt.objects.create(
            order=order,
            kind=RefundAttemptKind.STORED_VALUE,
            charge=None,
            amount=_eur("10.00"),
        )

        def titles():
            return [
                str(a["title"])
                for a in _context(widgets.StoreAlerts, _superuser())["alerts"]
            ]

        assert "Gift card and points refunds left unfinished" not in titles()
        RefundAttempt.objects.filter(pk=attempt.pk).update(
            created_at=attempt.created_at - timedelta(days=1)
        )
        assert "Gift card and points refunds left unfinished" in titles()
