import pytest
from djmoney.money import Money

from order.enum.status import PaymentStatus
from order.factories.order import OrderFactory

pytestmark = pytest.mark.django_db


def test_a_completed_payment_always_has_money_on_it():
    """``Order.clean()`` refuses a COMPLETED order with nothing paid
    unless deductions cover the total. The default ``paid_amount`` was
    0 for 30% of orders whatever the status, so ~4% of default orders
    were invalid and any test that validated them flaked."""
    for _ in range(100):
        order = OrderFactory.build(payment_status=PaymentStatus.COMPLETED)
        assert order.paid_amount.amount > 0


def test_an_explicit_paid_amount_still_wins():
    order = OrderFactory.build(
        payment_status=PaymentStatus.COMPLETED,
        paid_amount=Money(0, "EUR"),
    )

    assert order.paid_amount.amount == 0
