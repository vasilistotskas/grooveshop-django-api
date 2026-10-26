"""A lost dispute whose goods came back owes the receipt a credit (11.4).

ν.4308/2014 art. 12(4): a credit document is issued "in the case of
discounts or refunds". A chargeback by itself is the card network taking
money back, so the sale stands when the customer kept the goods; when the
goods came back (or never arrived) the sale is reversed in fact. Staff say
which, once per order, and the decision runs through the SAME credit path as
a refund (``create_pending_credits``): capped at what the receipt still
holds, on the same receipt-row cut-off, idempotent.

AADE's ``SendInvoices`` is the only faked seam (``tests.utils.mydata``).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import RequestFactory
from djmoney.money import Money

from admin.dashboard.store import queries
from order.admin import (
    ChargebackDecisionFilter,
    OrderAdmin,
    PaymentTransactionInline,
)
from order.chargebacks import (
    ChargebackDecisionError,
    decide_chargebacks,
)
from order.credit_documents import create_pending_credits
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus
from order.factories import PaymentTransactionFactory
from order.invoicing import ReceiptNotIssuable, generate_invoice
from order.models import (
    ChargebackDecision,
    Invoice,
    Order,
    OrderHistory,
    RetailCredit,
)
from order.models.invoice import MyDataStatus
from order.models.retail_credit import CreditReason
from order.payment_ledger import PaymentLedger
from tests.integration.order.test_mydata_lifecycle import (
    _credits,
    _eur,
    _mark_shipped,
    _order,
    _receipt,
)
from tests.utils.mydata import FakeAade, mydata_enabled
from user.factories import UserAccountFactory

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


@pytest.fixture
def staff():
    return UserAccountFactory(is_staff=True, is_superuser=True)


def _chargeback(order, amount: str = "100.00", reference: str = "dp_1"):
    """A lost dispute on the order's payment, as the ledger records it."""
    return PaymentTransactionFactory(
        order=order,
        charge=order.payment_transactions.get(
            kind=PaymentTransactionKind.CHARGE
        ),
        kind=PaymentTransactionKind.CHARGEBACK,
        amount=_eur(amount),
        provider_reference=reference,
    )


class TestGoodsReturnedCreditsTheReceipt:
    def test_one_credit_for_what_the_chargeback_took(self, staff):
        with mydata_enabled(), FakeAade() as aade:
            order = _order(lines=(("24", "100.00"),))
            _receipt(order)
            chargeback = _chargeback(order, "100.00")

            decide_chargebacks(order.id, goods_returned=True, user=staff)

        [credit] = _credits(order)
        [sent] = aade.credits
        assert sent.invoice_type == "11.4"
        # AADE forbids correlatedInvoices on an 11.4 (error 205).
        assert sent.correlated is None
        assert sent.gross == Decimal("100.00")
        assert credit.reason == CreditReason.CHARGEBACK
        assert credit.chargeback == chargeback
        assert credit.refund is None
        assert credit.invoice == Invoice.objects.get(order=order)
        assert credit.total == _eur("100.00")
        assert credit.mydata_status == MyDataStatus.CONFIRMED

    def test_a_part_chargeback_credits_only_that_part(self, staff):
        with mydata_enabled(), FakeAade() as aade:
            order = _order(lines=(("24", "100.00"),))
            _receipt(order)
            _chargeback(order, "40.00")

            decide_chargebacks(order.id, goods_returned=True, user=staff)

        [sent] = aade.credits
        assert sent.gross == Decimal("40.00")

    def test_deciding_twice_credits_once(self, staff):
        with mydata_enabled(), FakeAade() as aade:
            order = _order(lines=(("24", "100.00"),))
            _receipt(order)
            _chargeback(order)

            first = decide_chargebacks(
                order.id, goods_returned=True, user=staff
            )
            second = decide_chargebacks(
                order.id, goods_returned=True, user=staff
            )
            create_pending_credits(order.id)

        assert len(first) == 1
        assert second == []
        assert len(aade.credits) == 1
        assert _credits(order).count() == 1

    def test_the_credit_is_capped_at_what_the_receipt_holds(self, staff):
        """The receipt already has 40 credited, so a chargeback of 80 can
        credit no more than the 60 left."""
        with mydata_enabled(), FakeAade() as aade:
            order = _order(lines=(("24", "100.00"),))
            _receipt(order)
            charge = order.payment_transactions.get(
                kind=PaymentTransactionKind.CHARGE
            )
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("40.00"), reference="re_1"
            )
            create_pending_credits(order.id)
            _chargeback(order, "80.00")

            decide_chargebacks(order.id, goods_returned=True, user=staff)

        chargeback_credit = RetailCredit.objects.get(
            order=order, reason=CreditReason.CHARGEBACK
        )
        assert chargeback_credit.total == _eur("60.00")
        assert sum(c.total.amount for c in _credits(order)) == Decimal("100.00")
        assert len(aade.credits) == 2

    def test_a_receipt_with_nothing_left_is_not_over_credited(self, staff):
        with mydata_enabled(), FakeAade() as aade:
            order = _order(lines=(("24", "100.00"),))
            _receipt(order)
            charge = order.payment_transactions.get(
                kind=PaymentTransactionKind.CHARGE
            )
            PaymentLedger.record_refund(
                order, charge=charge, amount=_eur("100.00"), reference="re_1"
            )
            create_pending_credits(order.id)
            _chargeback(order, "50.00")

            decide_chargebacks(order.id, goods_returned=True, user=staff)

        assert not RetailCredit.objects.filter(
            order=order, reason=CreditReason.CHARGEBACK
        ).exists()
        assert len(aade.credits) == 1
        assert OrderHistory.objects.filter(
            order=order, new_value__note__icontains="No credit document"
        ).exists()

    def test_a_store_that_issues_by_hand_gets_the_credit_from_its_action(
        self, staff
    ):
        with mydata_enabled(auto_submit=False), FakeAade() as aade:
            order = _order(lines=(("24", "100.00"),))
            _receipt(order)
            _chargeback(order)

            decide_chargebacks(order.id, goods_returned=True, user=staff)
            assert aade.credits == []

            create_pending_credits(order.id)

        assert _credits(order).count() == 1


class TestCustomerKeptTheGoods:
    def test_no_credit_and_the_decision_is_audited(self, staff):
        with mydata_enabled(), FakeAade() as aade:
            order = _order(lines=(("24", "100.00"),))
            _receipt(order)
            chargeback = _chargeback(order)

            decide_chargebacks(order.id, goods_returned=False, user=staff)
            # Any later reconciliation (a refund, the receipt registering)
            # runs the same function: the kept goods must still owe nothing.
            create_pending_credits(order.id)

        assert aade.credits == []
        assert _credits(order).count() == 0
        decision = ChargebackDecision.objects.get(chargeback=chargeback)
        assert decision.goods_returned is False
        assert decision.decided_by_id == staff.pk
        assert decision.decided_at is not None
        assert OrderHistory.objects.filter(
            order=order, new_value__note__icontains="kept the goods"
        ).exists()

    def test_it_can_still_become_returned_after_the_receipt(self, staff):
        with mydata_enabled(), FakeAade() as aade:
            order = _order(lines=(("24", "100.00"),))
            _receipt(order)
            _chargeback(order)
            decide_chargebacks(order.id, goods_returned=False, user=staff)

            decide_chargebacks(order.id, goods_returned=True, user=staff)

        assert len(aade.credits) == 1


class TestTheDecisionCannotBeTakenBack:
    def test_returned_cannot_become_kept_once_the_receipt_exists(self, staff):
        with mydata_enabled(), FakeAade() as aade:
            order = _order(lines=(("24", "100.00"),))
            _receipt(order)
            chargeback = _chargeback(order)
            decide_chargebacks(order.id, goods_returned=True, user=staff)

            with pytest.raises(ChargebackDecisionError, match="receipt"):
                decide_chargebacks(order.id, goods_returned=False, user=staff)

        assert (
            ChargebackDecision.objects.get(chargeback=chargeback).goods_returned
            is True
        )
        assert len(aade.credits) == 1

    def test_an_order_without_a_chargeback_has_nothing_to_decide(self, staff):
        order = _order(lines=(("24", "100.00"),))

        with pytest.raises(ChargebackDecisionError, match="no chargeback"):
            decide_chargebacks(order.id, goods_returned=True, user=staff)


class TestNoReceiptYetNetsTheReceipt:
    def test_a_returned_chargeback_reduces_the_receipt_and_is_not_credited(
        self, staff
    ):
        order = _order(lines=(("24", "100.00"),))
        _chargeback(order, "30.00")
        decide_chargebacks(order.id, goods_returned=True, user=staff)

        with mydata_enabled(), FakeAade() as aade:
            _mark_shipped(order)
            generate_invoice(order)
            from order.tasks import send_invoice_to_mydata

            send_invoice_to_mydata(order.id)
            create_pending_credits(order.id)

        [receipt] = aade.receipts
        assert receipt.gross == Decimal("70.00")
        assert aade.credits == []
        assert _credits(order).count() == 0

    def test_an_undecided_chargeback_leaves_the_receipt_whole(self):
        order = _order(lines=(("24", "100.00"),))
        _chargeback(order, "30.00")

        with mydata_enabled(), FakeAade() as aade:
            _mark_shipped(order)
            generate_invoice(order)
            from order.tasks import send_invoice_to_mydata

            send_invoice_to_mydata(order.id)

        [receipt] = aade.receipts
        assert receipt.gross == Decimal("100.00")

    def test_a_kept_chargeback_leaves_the_receipt_whole(self, staff):
        order = _order(lines=(("24", "100.00"),))
        _chargeback(order, "30.00")
        decide_chargebacks(order.id, goods_returned=False, user=staff)

        with mydata_enabled(), FakeAade() as aade:
            _mark_shipped(order)
            generate_invoice(order)
            from order.tasks import send_invoice_to_mydata

            send_invoice_to_mydata(order.id)

        [receipt] = aade.receipts
        assert receipt.gross == Decimal("100.00")

    def test_everything_charged_back_and_returned_means_no_receipt(self, staff):
        order = _order(lines=(("24", "100.00"),))
        _chargeback(order, "100.00")
        decide_chargebacks(order.id, goods_returned=True, user=staff)
        _mark_shipped(order)

        with mydata_enabled(), FakeAade():
            with pytest.raises(ReceiptNotIssuable):
                generate_invoice(order)

    def test_it_can_be_taken_back_until_the_receipt_exists(self, staff):
        order = _order(lines=(("24", "100.00"),))
        _chargeback(order, "30.00")
        decide_chargebacks(order.id, goods_returned=True, user=staff)

        decide_chargebacks(order.id, goods_returned=False, user=staff)

        assert PaymentLedger.chargebacks_returned(order) == Decimal(0)


class TestLedgerFigure:
    def test_only_returned_decisions_count_and_by_their_moment(self, staff):
        order = _order(lines=(("24", "100.00"),))
        returned = _chargeback(order, "30.00", "dp_a")
        _chargeback(order, "20.00", "dp_b")
        ChargebackDecision.objects.create(
            chargeback=returned, goods_returned=True, decided_by=staff
        )

        assert PaymentLedger.chargebacks_returned(order) == Decimal("30.00")
        assert PaymentLedger.charged_back(order) == Decimal("50.00")
        decided_at = ChargebackDecision.objects.get().decided_at
        assert PaymentLedger.chargebacks_returned(
            order, before=decided_at
        ) == Decimal(0)


class TestAwaitingADecision:
    def test_the_dashboard_counts_orders_until_each_is_decided(self, staff):
        order = _order(lines=(("24", "100.00"),))
        _chargeback(order)
        # Paid on delivery: no dispute to decide, so never counted.
        _order(lines=(("24", "50.00"),), paid_online=False)

        assert queries.attention_counts.compute()["chargebacks_to_settle"] == 1
        assert list(Order.objects.awaiting_chargeback_decision()) == [order]

        decide_chargebacks(order.id, goods_returned=False, user=staff)

        assert queries.attention_counts.compute()["chargebacks_to_settle"] == 0
        assert not Order.objects.awaiting_chargeback_decision().exists()

    def test_an_order_with_two_chargebacks_is_counted_once(self):
        order = _order(lines=(("24", "100.00"),))
        _chargeback(order, "30.00", "dp_a")
        _chargeback(order, "20.00", "dp_b")

        assert queries.attention_counts.compute()["chargebacks_to_settle"] == 1

    def test_the_order_list_filter(self):
        order = _order(lines=(("24", "100.00"),))
        _chargeback(order)
        _order(lines=(("24", "50.00"),), paid_online=False)
        admin = OrderAdmin(Order, AdminSite())
        request = RequestFactory().get("/admin/order/order/")
        filter_ = ChargebackDecisionFilter(
            request, {"chargeback": ["undecided"]}, Order, admin
        )

        assert list(filter_.queryset(request, Order.objects.all())) == [order]


class TestTheAdminAction:
    @staticmethod
    def _post(admin, staff, order, outcome):
        request = RequestFactory().post(
            "/admin/order/order/",
            {"_form_submitted": "true", "outcome": outcome},
        )
        request.user = staff
        request.session = {}
        request._messages = FallbackStorage(request)
        return admin.decide_chargeback(request, object_id=order.pk), request

    def test_returned_credits_the_receipt(self, staff):
        admin = OrderAdmin(Order, AdminSite())
        with mydata_enabled(), FakeAade() as aade:
            order = _order(lines=(("24", "100.00"),))
            _receipt(order)
            _chargeback(order)

            response, _request = self._post(admin, staff, order, "returned")

        assert response.status_code == 200
        assert len(aade.credits) == 1

    def test_kept_credits_nothing(self, staff):
        admin = OrderAdmin(Order, AdminSite())
        with mydata_enabled(), FakeAade() as aade:
            order = _order(lines=(("24", "100.00"),))
            _receipt(order)
            _chargeback(order)

            self._post(admin, staff, order, "kept")

        assert aade.credits == []
        assert ChargebackDecision.objects.get().goods_returned is False

    def test_an_empty_choice_decides_nothing(self, staff):
        admin = OrderAdmin(Order, AdminSite())
        order = _order(lines=(("24", "100.00"),))
        _chargeback(order)

        self._post(admin, staff, order, "")

        assert not ChargebackDecision.objects.exists()

    def test_a_refused_change_is_reported_not_raised(self, staff):
        admin = OrderAdmin(Order, AdminSite())
        with mydata_enabled(), FakeAade():
            order = _order(lines=(("24", "100.00"),))
            _receipt(order)
            _chargeback(order)
            self._post(admin, staff, order, "returned")

            response, request = self._post(admin, staff, order, "kept")

        assert response.status_code == 200
        assert any("receipt" in str(message) for message in request._messages)
        assert ChargebackDecision.objects.get().goods_returned is True

    def test_it_is_offered_only_where_a_dispute_was_lost(self, staff):
        admin = OrderAdmin(Order, AdminSite())
        request = RequestFactory().get("/")
        request.user = staff
        plain = _order(lines=(("24", "100.00"),), paid_online=False)
        lost = _order(lines=(("24", "100.00"),))
        _chargeback(lost, "10.00")

        assert not admin.has_decide_chargeback_permission(request, plain.pk)
        assert admin.has_decide_chargeback_permission(request, lost.pk)

    def test_the_ledger_inline_shows_what_was_decided(self, staff):
        inline = PaymentTransactionInline(
            PaymentTransactionInline.model, AdminSite()
        )
        order = _order(lines=(("24", "100.00"),))
        chargeback = _chargeback(order)

        assert str(inline.decision_display(chargeback)) == (
            "Awaiting a decision"
        )
        ChargebackDecision.objects.create(
            chargeback=chargeback, goods_returned=True, decided_by=staff
        )
        chargeback.refresh_from_db()
        assert str(inline.decision_display(chargeback)).startswith(
            "Returned or not delivered"
        )
        assert (
            inline.decision_display(
                order.payment_transactions.get(
                    kind=PaymentTransactionKind.CHARGE
                )
            )
            == ""
        )


def test_the_credit_reason_pairs_with_its_source():
    """A credit names exactly one source (a refund, a stored-value refund
    or a chargeback); the database refuses any other pairing."""
    from django.db import IntegrityError, transaction

    order = _order(lines=(("24", "100.00"),))
    with mydata_enabled(), FakeAade():
        invoice = _receipt(order)
    chargeback = _chargeback(order)

    with pytest.raises(IntegrityError), transaction.atomic():
        RetailCredit.objects.create(
            order=order,
            invoice=invoice,
            reason=CreditReason.CHARGEBACK,
            credit_number="CRD-TEST-000001",
            subtotal=Money(Decimal(1), "EUR"),
            total_vat=Money(Decimal(0), "EUR"),
            total=Money(Decimal(1), "EUR"),
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        RetailCredit.objects.create(
            order=order,
            invoice=invoice,
            reason=CreditReason.REFUND,
            chargeback=chargeback,
            credit_number="CRD-TEST-000002",
            subtotal=Money(Decimal(1), "EUR"),
            total_vat=Money(Decimal(0), "EUR"),
            total=Money(Decimal(1), "EUR"),
        )


def test_status_is_untouched_by_a_decision(staff):
    order = _order(lines=(("24", "100.00"),))
    _chargeback(order)
    before = Order.objects.get(pk=order.pk)

    decide_chargebacks(order.id, goods_returned=True, user=staff)

    after = Order.objects.get(pk=order.pk)
    assert (after.status, after.payment_status) == (
        before.status,
        before.payment_status,
    )
    assert after.status == OrderStatus.PROCESSING
