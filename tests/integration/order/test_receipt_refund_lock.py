"""A refund and the receipt that nets it cannot interleave.

The receipt nets the refunds the ledger holds when it is built, and a refund
made after its row is credited by comparing ``created_at``. A refund that is
inserted but not yet committed while the receipt is built is invisible to
the netting yet older than the row: nothing would ever account for it. The
receipt therefore takes the order lock every refund path holds, and these
tests run both sides on real connections.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import connection, transaction
from djmoney.money import Money

from order.enum.document_type import OrderDocumentTypeEnum
from order.enum.payment_transaction import PaymentTransactionKind
from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory, PaymentTransactionFactory
from order.factories.item import OrderItemFactory
from order.invoicing import ReceiptNotIssuable, generate_invoice
from order.models import Invoice, Order
from order.payment_ledger import PaymentLedger
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from tests.utils.mydata import mydata_enabled
from vat.factories import VatFactory

pytestmark = [
    pytest.mark.django_db(transaction=True),
    pytest.mark.assert_english,
]

PI = "pi_receipt_lock"
WAIT = 10


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def _shipped_order():
    order = OrderFactory(
        num_order_items=0,
        document_type=OrderDocumentTypeEnum.RECEIPT,
        status=OrderStatus.SHIPPED,
        pay_way=PayWayFactory.create_online_payment(),
        payment_status=PaymentStatus.COMPLETED,
        payment_id=PI,
        payment_method="stripe",
    )
    Order.objects.filter(pk=order.pk).update(
        shipping_price=_eur("0.00"),
        payment_method_fee=_eur("0.00"),
        amount_due=_eur("100.00"),
    )
    OrderItemFactory(
        order=order,
        product=ProductFactory(vat=VatFactory(value=Decimal(24))),
        price=_eur("100.00"),
        quantity=1,
    )
    PaymentTransactionFactory(
        order=order, amount=_eur("100.00"), provider_reference=PI
    )
    order.refresh_from_db()
    return order


class _RefundInFlight:
    """A refund on its own connection: the order locked, its ledger row
    written, the transaction held open until ``release`` is set."""

    def __init__(self, order: Order, amount: str) -> None:
        self.order_id = order.pk
        self.amount = _eur(amount)
        self.inserted = threading.Event()
        self.release = threading.Event()
        self.errors: list[BaseException] = []
        self.thread = threading.Thread(target=self._run)

    def _run(self) -> None:
        try:
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=self.order_id)
                charge = order.payment_transactions.get(
                    kind=PaymentTransactionKind.CHARGE
                )
                PaymentLedger.record_refund(
                    order,
                    charge=charge,
                    amount=self.amount,
                    reference="re_receipt_lock",
                )
                self.inserted.set()
                self.release.wait(WAIT)
        except BaseException as exc:
            self.errors.append(exc)
            self.inserted.set()
        finally:
            connection.close()

    def start(self) -> None:
        self.thread.start()
        assert self.inserted.wait(WAIT)

    def finish(self) -> None:
        self.release.set()
        self.thread.join(WAIT)
        assert not self.errors, self.errors


def _issue_in_thread(order: Order, outcome: list) -> threading.Thread:
    def run() -> None:
        try:
            with mydata_enabled():
                outcome.append(generate_invoice(order))
        except BaseException as exc:
            outcome.append(exc)
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    return thread


@pytest.fixture(autouse=True)
def _render():
    with patch(
        "order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test"
    ):
        yield


def test_a_refund_in_flight_is_netted_by_the_receipt_built_meanwhile():
    order = _shipped_order()
    refund = _RefundInFlight(order, "30.00")
    refund.start()

    outcome: list = []
    issuing = _issue_in_thread(order, outcome)
    # Unlocked, the receipt is built and committed here, blind to the
    # refund; locked, it waits for the refund to commit.
    issuing.join(1.5)
    refund.finish()
    issuing.join(WAIT)

    [invoice] = outcome
    assert isinstance(invoice, Invoice), invoice
    assert invoice.total == _eur("70.00")


def test_a_refund_in_flight_that_returns_the_whole_sale_refuses_the_receipt():
    order = _shipped_order()
    refund = _RefundInFlight(order, "100.00")
    refund.start()

    outcome: list = []
    issuing = _issue_in_thread(order, outcome)
    issuing.join(1.5)
    refund.finish()
    issuing.join(WAIT)

    [result] = outcome
    assert isinstance(result, ReceiptNotIssuable), result
    assert not Invoice.objects.filter(order=order).exists()
