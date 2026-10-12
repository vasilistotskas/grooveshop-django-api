import pytest
from djmoney.money import Money

from order.enum.status import PaymentStatus
from order.factories.order import OrderFactory

pytestmark = pytest.mark.django_db


def test_a_completed_payment_always_has_money_on_it():
    """``Order.clean()`` refuses a COMPLETED order with nothing due
    unless deductions cover the total. The default ``amount_due`` was
    0 for 30% of orders whatever the status, so ~4% of default orders
    were invalid and any test that validated them flaked."""
    for _ in range(100):
        order = OrderFactory.build(payment_status=PaymentStatus.COMPLETED)
        assert order.amount_due.amount > 0


def test_an_explicit_amount_due_still_wins():
    order = OrderFactory.build(
        payment_status=PaymentStatus.COMPLETED,
        amount_due=Money(0, "EUR"),
    )

    assert order.amount_due.amount == 0
