"""Refunding cash a courier collected on delivery.

The money of a cash-on-delivery order enters the ledger as a
``COD_COLLECTION`` row, and there is no provider to refund it through: the
merchant pays the customer back (a bank transfer, cash) and says how. That
refund goes through the same ``OrderService.refund_order`` and the same
staff action as any other, is one REFUND row and one ``order_refunded``, so
the myDATA 11.4 credit note, the email and the rest follow as they do for a
Stripe or Viva refund.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory
from djmoney.money import Money

from order.admin import OrderAdmin, RefundOrderForm
from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import PaymentError
from order.factories import OrderFactory, PaymentTransactionFactory
from order.factories.item import OrderItemFactory
from order.invoicing import generate_invoice
from order.models import Invoice, Order, RetailCredit
from order.models.invoice import MyDataStatus
from order.models.retail_credit import CreditReason
from order.payment_ledger import (
    MANUAL_REFUND_PROVIDER,
    REPAYMENT_REFERENCE_MAX_LENGTH,
    PaymentLedger,
)
from order.services import OrderService
from order.tasks import send_invoice_to_mydata
from product.factories.product import ProductFactory
from tests.utils.mydata import FakeAade, mydata_enabled
from tests.utils.orders import courier_cash_order
from tests.utils.stripe_refunds import FakeStripeRefunds
from user.factories import UserAccountFactory
from vat.factories import VatFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

REFUND = PaymentTransactionKind.REFUND
COLLECTION = PaymentTransactionKind.COD_COLLECTION


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def _cod_order(collected="100.00", **kwargs):
    """A delivered cash-on-delivery order whose courier payout was
    reconciled: paid, with the collection on the ledger."""
    kwargs.setdefault("status", OrderStatus.DELIVERED)
    kwargs.setdefault("payment_status", PaymentStatus.COMPLETED)
    kwargs.setdefault("payment_id", "")
    kwargs.setdefault("amount_due", _eur(collected))
    order = courier_cash_order(**kwargs)
    PaymentTransactionFactory(
        order=order,
        kind=COLLECTION,
        provider="acs_cod",
        provider_reference=f"VOUCHER-{order.pk}",
        amount=_eur(collected),
    )
    return order


def _status(order) -> str:
    return Order.objects.values_list("payment_status", flat=True).get(
        pk=order.pk
    )


def _refund_rows(order):
    return order.payment_transactions.filter(kind=REFUND).order_by("id")


class TestWhatIsRefundableToday:
    def test_collected_cash_is_received_money_the_ledger_can_refund(self):
        order = _cod_order("40.00")

        assert PaymentLedger.captured(order) == Decimal("40.00")
        assert PaymentLedger.refundable(order) == Decimal("40.00")
        [balance] = PaymentLedger.balances(order)
        assert balance.charge.kind == COLLECTION
        [(charge, share)] = PaymentLedger.plan_refund(order, None)
        assert charge.kind == COLLECTION
        assert share == _eur("40.00")


class TestRefundingCollectedCash:
    def test_a_full_refund_is_recorded_as_one_manual_refund(
        self, refunded_signals
    ):
        order = _cod_order("100.00")

        result = OrderService.refund_order(
            order, reason="returned", repayment_reference="TRX-1"
        )

        assert result.complete
        assert result.refunded == _eur("100.00")
        assert _status(order) == PaymentStatus.REFUNDED
        [refund] = _refund_rows(order)
        collection = order.payment_transactions.get(kind=COLLECTION)
        assert refund.charge == collection
        assert refund.provider == MANUAL_REFUND_PROVIDER
        assert refund.provider_reference == (
            f"order-{order.pk}-{collection.pk}-TRX-1"
        )
        assert refund.amount == _eur("100.00")
        assert refund.description == "returned"
        assert result.refund_ids == (refund.provider_reference,)
        assert PaymentLedger.refundable(order) == 0
        # One order_refunded, carrying the ledger row.
        [signal] = refunded_signals
        assert signal["refund"] == refund
        assert signal["amount"] == _eur("100.00")

    def test_two_part_refunds_then_nothing_is_left(self, refunded_signals):
        order = _cod_order("100.00")

        first = OrderService.refund_order(
            order, amount=_eur("30.00"), repayment_reference="TRX-A"
        )
        assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED
        second = OrderService.refund_order(
            order, amount=_eur("70.00"), repayment_reference="TRX-B"
        )

        assert [first.refunded, second.refunded] == [
            _eur("30.00"),
            _eur("70.00"),
        ]
        assert _status(order) == PaymentStatus.REFUNDED
        assert [r.amount for r in _refund_rows(order)] == [
            _eur("30.00"),
            _eur("70.00"),
        ]
        assert len(refunded_signals) == 2

        with pytest.raises(PaymentError) as excinfo:
            OrderService.refund_order(order, repayment_reference="TRX-C")
        assert str(excinfo.value) == "This order has already been refunded."

    def test_more_than_the_remainder_is_refused_and_records_nothing(
        self, refunded_signals
    ):
        order = _cod_order("100.00")
        OrderService.refund_order(
            order, amount=_eur("60.00"), repayment_reference="TRX-A"
        )

        with pytest.raises(PaymentError) as excinfo:
            OrderService.refund_order(
                order, amount=_eur("40.01"), repayment_reference="TRX-B"
            )

        assert "cannot exceed the refundable amount (40.00)" in str(
            excinfo.value
        )
        assert _refund_rows(order).count() == 1
        assert len(refunded_signals) == 1

    def test_how_it_was_paid_back_is_required(self, refunded_signals):
        order = _cod_order("100.00")

        for blank in ("", "   "):
            with pytest.raises(PaymentError) as excinfo:
                OrderService.refund_order(
                    order, amount=_eur("10.00"), repayment_reference=blank
                )
            assert str(excinfo.value).startswith("Say how the cash was paid")

        assert not _refund_rows(order).exists()
        assert refunded_signals == []
        assert _status(order) == PaymentStatus.COMPLETED

    def test_a_reference_longer_than_the_ledger_takes_is_refused(self):
        order = _cod_order("100.00")

        with pytest.raises(PaymentError):
            OrderService.refund_order(
                order,
                amount=_eur("10.00"),
                repayment_reference="x" * (REPAYMENT_REFERENCE_MAX_LENGTH + 1),
            )

        assert not _refund_rows(order).exists()

    def test_a_double_submit_does_not_book_two_refunds(self, refunded_signals):
        order = _cod_order("100.00")
        OrderService.refund_order(
            order, amount=_eur("30.00"), repayment_reference="TRX-1"
        )

        with pytest.raises(PaymentError) as excinfo:
            OrderService.refund_order(
                order, amount=_eur("30.00"), repayment_reference="TRX-1"
            )

        assert str(excinfo.value) == (
            "A refund with this reference is already recorded for this order."
        )
        assert _refund_rows(order).count() == 1
        assert len(refunded_signals) == 1
        assert PaymentLedger.refundable(order) == Decimal("70.00")

    def test_the_same_reference_on_another_order_is_its_own_refund(self):
        first, second = _cod_order("20.00"), _cod_order("20.00")

        for order in (first, second):
            OrderService.refund_order(order, repayment_reference="TRX-1")

        assert _refund_rows(first).count() == _refund_rows(second).count() == 1

    def test_a_reference_is_ignored_when_no_cash_is_refunded(self):
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="pi_cod_test",
        )
        PaymentTransactionFactory(
            order=order, amount=_eur("50.00"), provider_reference="pi_cod_test"
        )

        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(
                order, amount=_eur("10.00"), repayment_reference="unused"
            )

        assert len(stripe_api.created) == 1
        [refund] = _refund_rows(order)
        assert refund.provider == "stripe"


class TestUncollectedCash:
    def test_nothing_is_refundable_before_the_courier_pays_out(self):
        order = courier_cash_order(
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.PENDING,
            payment_id="",
        )

        assert PaymentLedger.refundable(order) == 0
        with pytest.raises(PaymentError) as excinfo:
            OrderService.refund_order(order, repayment_reference="TRX-1")

        assert str(excinfo.value) == (
            "The payment on delivery has not been collected yet, so there "
            "is nothing to refund."
        )
        assert not order.payment_transactions.exists()


class TestMixedProviderAndCash:
    def _mixed(self):
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="pi_mixed",
        )
        PaymentTransactionFactory(
            order=order, amount=_eur("60.00"), provider_reference="pi_mixed"
        )
        PaymentTransactionFactory(
            order=order,
            kind=COLLECTION,
            provider="acs_cod",
            provider_reference="VOUCHER-2",
            amount=_eur("40.00"),
        )
        return order

    def test_each_share_goes_where_its_money_came_from(self, refunded_signals):
        order = self._mixed()

        with FakeStripeRefunds() as stripe_api:
            result = OrderService.refund_order(
                order, repayment_reference="TRX-MIX"
            )

        assert result.complete
        assert result.refunded == _eur("100.00")
        assert stripe_api.amounts == [6000]
        assert [(r.provider, r.amount) for r in _refund_rows(order)] == [
            ("stripe", _eur("60.00")),
            (MANUAL_REFUND_PROVIDER, _eur("40.00")),
        ]
        assert _status(order) == PaymentStatus.REFUNDED
        assert len(refunded_signals) == 2

    def test_a_missing_reference_refuses_before_the_provider_is_called(self):
        order = self._mixed()

        with FakeStripeRefunds() as stripe_api:
            with pytest.raises(PaymentError):
                OrderService.refund_order(order)

        assert stripe_api.calls == []
        assert not _refund_rows(order).exists()

    def test_an_amount_inside_the_provider_payment_needs_no_reference(self):
        order = self._mixed()

        with FakeStripeRefunds() as stripe_api:
            OrderService.refund_order(order, amount=_eur("25.00"))

        assert stripe_api.amounts == [2500]
        assert _status(order) == PaymentStatus.PARTIALLY_REFUNDED


class TestTheCreditNote:
    """The 11.4 credit document follows the refund row, like any refund."""

    def _shipped_cod_order(self):
        order = courier_cash_order(
            document_type=OrderDocumentTypeEnum.RECEIPT,
            status=OrderStatus.PROCESSING,
            payment_status=PaymentStatus.PENDING,
            payment_id="",
            amount_due=_eur("124.00"),
        )
        # The receipt is the one 124.00 line, with no shipping or fee.
        Order.objects.filter(pk=order.pk).update(
            amount_due=_eur("124.00"),
            shipping_price=_eur("0.00"),
            payment_method_fee=_eur("0.00"),
        )
        OrderItemFactory(
            order=order,
            product=ProductFactory(vat=VatFactory(value=Decimal(24))),
            price=_eur("124.00"),
            quantity=1,
        )
        Order.objects.filter(pk=order.pk).update(status=OrderStatus.SHIPPED)
        order.refresh_from_db()
        order._original_status = order.status
        generate_invoice(order)
        send_invoice_to_mydata(order.id)
        assert (
            Invoice.objects.get(order=order).mydata_status
            == MyDataStatus.CONFIRMED
        )
        # The courier pays out later: the collection lands on the ledger.
        OrderService.settle_cod_payment(
            order,
            amount=_eur("124.00"),
            provider="acs_cod",
            reference="VOUCHER-3",
        )
        order.refresh_from_db()
        return order

    def test_a_refund_of_cash_issues_one_credit_note_per_refund(self):
        with (
            patch(
                "order.invoicing._render_pdf_bytes",
                return_value=b"%PDF-1.4 test",
            ),
            mydata_enabled(),
            FakeAade() as aade,
        ):
            order = self._shipped_cod_order()
            OrderService.refund_order(
                order,
                amount=_eur("24.00"),
                reason="one item back",
                repayment_reference="IBAN-TRX-1",
            )
            OrderService.refund_order(
                order, reason="the rest", repayment_reference="IBAN-TRX-2"
            )

        notes = list(RetailCredit.objects.filter(order=order).order_by("id"))
        assert [c.total for c in notes] == [_eur("24.00"), _eur("100.00")]
        assert [(s.invoice_type, s.gross) for s in aade.credits] == [
            ("11.4", Decimal("24.00")),
            ("11.4", Decimal("100.00")),
        ]
        # AADE forbids correlatedInvoices on an 11.4 (error 205).
        assert all(s.correlated is None for s in aade.credits)
        assert all(c.reason == CreditReason.REFUND for c in notes)
        assert [c.refund.provider for c in notes] == [
            MANUAL_REFUND_PROVIDER
        ] * 2
        assert all(c.mydata_status == MyDataStatus.CONFIRMED for c in notes)
        assert _status(order) == PaymentStatus.REFUNDED


def _request(user, data):
    request = RequestFactory().post(
        "/admin/order/order/", {"_form_submitted": "true", **data}
    )
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


def _form(user, order, data):
    # ``_form_submitted`` is BaseDialogForm's required hidden field.
    return RefundOrderForm(
        _request(user, data),
        order.pk,
        data={"_form_submitted": "true", **data},
    )


@pytest.fixture
def staff_user():
    return UserAccountFactory(is_staff=True, is_superuser=True)


class TestTheStaffAction:
    def test_the_dialog_asks_for_the_reference_only_where_cash_is_refundable(
        self, staff_user
    ):
        cash = _cod_order("50.00")
        card = OrderFactory(
            num_order_items=0,
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
        )
        PaymentTransactionFactory(order=card, amount=_eur("50.00"))

        assert "repayment_reference" in _form(staff_user, cash, {}).fields
        assert "repayment_reference" not in _form(staff_user, card, {}).fields

    def test_a_refund_of_cash_without_a_reference_is_invalid(self, staff_user):
        order = _cod_order("50.00")

        form = _form(staff_user, order, {"amount": "20.00", "reason": "Return"})

        assert not form.is_valid()
        assert form.errors["repayment_reference"] == [
            (
                "Say how the cash was paid back: this refund returns money "
                "collected on delivery."
            )
        ]

    def test_a_refund_of_cash_with_a_reference_is_valid(self, staff_user):
        order = _cod_order("50.00")

        form = _form(
            staff_user,
            order,
            {
                "amount": "20.00",
                "reason": "Return",
                "repayment_reference": "TRX-9",
            },
        )

        assert form.is_valid(), form.errors

    def test_a_refund_inside_the_provider_payment_needs_no_reference(
        self, staff_user
    ):
        order = OrderFactory(
            num_order_items=0,
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.COMPLETED,
            payment_id="pi_form",
        )
        PaymentTransactionFactory(
            order=order, amount=_eur("60.00"), provider_reference="pi_form"
        )
        PaymentTransactionFactory(
            order=order,
            kind=COLLECTION,
            provider="acs_cod",
            provider_reference="VOUCHER-4",
            amount=_eur("40.00"),
        )

        inside = _form(staff_user, order, {"amount": "60.00", "reason": "r"})
        beyond = _form(staff_user, order, {"amount": "60.01", "reason": "r"})

        assert inside.is_valid(), inside.errors
        assert not beyond.is_valid()
        assert "repayment_reference" in beyond.errors

    def test_the_action_records_the_cash_refund(self, staff_user):
        order = _cod_order("50.00")
        request = _request(
            staff_user,
            {
                "amount": "50.00",
                "reason": "Customer returned the parcel",
                "repayment_reference": "TRX-ADMIN",
            },
        )

        response = OrderAdmin(Order, AdminSite()).refund_payment(
            request, object_id=order.pk
        )

        assert response.headers["HX-Redirect"].endswith(
            f"/order/order/{order.pk}/change/"
        )
        assert [str(m) for m in get_messages(request)] == [
            f"Order {order.pk} refunded €50.00."
        ]
        [refund] = _refund_rows(order)
        assert refund.provider == MANUAL_REFUND_PROVIDER
        assert refund.description == "Customer returned the parcel"
        assert _status(order) == PaymentStatus.REFUNDED

    def test_the_action_is_not_offered_when_the_cash_was_never_collected(
        self, staff_user
    ):
        """Nothing was collected, so nothing is refundable: the action is
        not offered (the service still says why, tested above)."""
        order = courier_cash_order(
            status=OrderStatus.DELIVERED,
            payment_status=PaymentStatus.PENDING,
            payment_id="",
        )
        request = _request(staff_user, {"amount": "10.00", "reason": "Return"})
        admin = OrderAdmin(Order, AdminSite())

        assert not admin.has_refund_permission(request, object_id=order.pk)
        with pytest.raises(PermissionDenied):
            admin.refund_payment(request, object_id=order.pk)
