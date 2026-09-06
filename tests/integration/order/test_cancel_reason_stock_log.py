"""A cancellation with no reason must not log a dangling colon.

``cancel_order`` takes ``reason: str = ""``, and the stock-restore call
interpolated it unconditionally:

    reason=f"Order {order.id} canceled: {reason}"

so a cancellation without a reason — the normal case from the admin —
wrote "Order 270 canceled:" into the stock log. Production order 270
carries exactly that. It reads as a truncated message precisely when
someone is chasing why stock moved.

The refund call a few lines below already guarded this; the stock call
simply never did.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.models.stock_log import StockLog
from order.services import OrderService
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory

pytestmark = pytest.mark.django_db


def _order():
    cart = CartFactory(is_guest=True)
    cart.items.all().delete()
    product = ProductFactory(
        stock=10,
        price=Money(Decimal("10.00"), "EUR"),
        discount_percent=Decimal(0),
        active=True,
    )
    CartItemFactory(cart=cart, product=product, quantity=2)
    country = CountryFactory()
    return OrderService.create_order_from_cart_offline(
        cart=cart,
        shipping_address={
            "first_name": "Maria",
            "last_name": "Papadopoulou",
            "email": "maria@example.com",
            "street": "Ermou",
            "street_number": "1",
            "city": "Athens",
            "zipcode": "10563",
            "country_id": country.alpha_2,
            "phone": "+306900000000",
        },
        pay_way=PayWayFactory(
            is_online_payment=False,
            active=True,
            cost=Money(Decimal(0), "EUR"),
            free_threshold=Money(Decimal(0), "EUR"),
        ),
        user=None,
    )


def _restore_reasons(order):
    return list(
        StockLog.objects.filter(
            order_id=order.id, operation_type="INCREMENT"
        ).values_list("reason", flat=True)
    )


class TestCancelReasonInStockLog:
    def test_no_reason_leaves_no_trailing_colon(self):
        order = _order()

        OrderService.cancel_order(order=order, refund_payment=False)

        reasons = _restore_reasons(order)
        assert reasons, "sanity: cancelling restored stock and logged it"
        for reason in reasons:
            assert not reason.rstrip().endswith(":"), (
                f"dangling colon in stock log: {reason!r}"
            )
            assert reason == f"Order {order.id} canceled"

    def test_a_supplied_reason_is_still_recorded(self):
        """The fix must not swallow the reason when there is one."""
        order = _order()

        OrderService.cancel_order(
            order=order, reason="customer changed mind", refund_payment=False
        )

        reasons = _restore_reasons(order)
        assert reasons
        for reason in reasons:
            assert reason == (
                f"Order {order.id} canceled: customer changed mind"
            )
