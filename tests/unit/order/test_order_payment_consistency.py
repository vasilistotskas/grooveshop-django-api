"""``Order.clean()``: a COMPLETED payment with nothing due is fully covered.

``amount_due`` 0.00 on a COMPLETED order is legitimate only when
deductions cover the whole total (``Order.is_paid``). The demo seed
produced eight COMPLETED orders with 0.00 and no deduction at all; the
admin form, which edits both fields, could produce the same.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from djmoney.money import Money

from order.enum.status import OrderStatus, PaymentStatus
from order.factories import OrderFactory

pytestmark = pytest.mark.django_db


def _order(payment_status, due):
    order = OrderFactory(
        status=OrderStatus.PROCESSING,
        payment_status=payment_status,
        num_order_items=1,
    )
    order.amount_due = Money(due, "EUR")
    return order


def test_completed_with_nothing_due_and_no_deduction_is_refused():
    order = _order(PaymentStatus.COMPLETED, "0.00")

    errors = order._payment_consistency_errors()

    assert list(errors) == ["amount_due"]


def test_clean_raises_it_on_the_amount_due_field():
    order = _order(PaymentStatus.COMPLETED, "0.00")

    with pytest.raises(ValidationError) as exc_info:
        order.clean()

    assert "amount_due" in exc_info.value.message_dict


def test_completed_and_fully_covered_by_a_gift_card_is_valid():
    order = _order(PaymentStatus.COMPLETED, "0.00")
    order.gift_card_amount = order.total_price

    assert order._payment_consistency_errors() == {}


def test_completed_with_an_amount_due_is_valid():
    order = _order(PaymentStatus.COMPLETED, "12.50")

    assert order._payment_consistency_errors() == {}


def test_an_unpaid_order_is_not_judged():
    order = _order(PaymentStatus.PENDING, "0.00")

    assert order._payment_consistency_errors() == {}
