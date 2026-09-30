"""``Order`` totals: items (aggregated in SQL) + extras, in one currency.

``total_price_items`` sums ``price * quantity`` over the item rows and
takes its currency from them; ``total_price_extra`` is shipping plus the
payment-method fee. ``total_price`` refuses to add the two when their
currencies differ rather than silently mixing them.
"""

from decimal import Decimal

import pytest
from django.conf import settings
from django.db.models.signals import post_save
from djmoney.money import Money
from factory.django import mute_signals

from order.factories import OrderFactory
from order.models.item import OrderItem
from order.models.order import Order
from product.models import Product

pytestmark = pytest.mark.django_db


def _order(shipping: str, lines=(), currency: str = "EUR", item_currency=None):
    """An order with explicit lines, created with signals muted: the
    creation handlers email the admin a total, which is not under test
    and cannot be computed for a deliberately inconsistent order."""
    with mute_signals(post_save):
        order = OrderFactory(
            shipping_price=Money(Decimal(shipping), currency),
            num_order_items=0,
        )
    for index, (price, quantity) in enumerate(lines):
        product = Product.objects.create(
            sku=f"TOTAL-{order.id}-{index}",
            price=Money(Decimal("1.00"), currency),
            stock=1000,
        )
        OrderItem.objects.create(
            order=order,
            product=product,
            price=Money(Decimal(price), item_currency or currency),
            quantity=quantity,
        )
    order.refresh_from_db()
    return order


@pytest.mark.parametrize(
    "lines,shipping,items_total,total",
    [
        ([("10.00", 1)], "0.00", "10.00", "10.00"),
        ([("10.00", 2), ("15.00", 1)], "5.00", "35.00", "40.00"),
        ([("9.99", 1), ("19.99", 2)], "4.99", "49.97", "54.96"),
        ([("0.00", 5), ("10.00", 1)], "5.00", "10.00", "15.00"),
        ([("999.99", 100)], "100.00", "99999.00", "100099.00"),
        ([], "5.00", "0.00", "5.00"),
    ],
    ids=[
        "single-line",
        "lines-plus-shipping",
        "cents",
        "free-line",
        "large",
        "no-lines",
    ],
)
def test_totals_add_lines_and_shipping(lines, shipping, items_total, total):
    order = _order(shipping, lines)

    assert order.total_price_items == Money(Decimal(items_total), "EUR")
    assert order.total_price_extra == Money(Decimal(shipping), "EUR")
    assert order.total_price == Money(Decimal(total), "EUR")
    assert order.calculate_order_total_amount() == order.total_price


@pytest.mark.parametrize("currency", settings.CURRENCIES)
def test_totals_keep_the_orders_currency(currency):
    order = _order("5.00", [("10.00", 2)], currency=currency)

    assert order.total_price_items == Money(Decimal("20.00"), currency)
    assert order.total_price_extra == Money(Decimal("5.00"), currency)
    assert order.total_price == Money(Decimal("25.00"), currency)


@pytest.mark.parametrize(
    "shipping_currency,item_currency",
    [
        (settings.CURRENCIES[0], settings.CURRENCIES[1]),
        (settings.CURRENCIES[1], settings.CURRENCIES[0]),
    ],
)
def test_total_refuses_to_mix_currencies(shipping_currency, item_currency):
    order = _order(
        "10.00",
        [("20.00", 2)],
        currency=shipping_currency,
        item_currency=item_currency,
    )

    with pytest.raises(ValueError, match="different currencies"):
        _ = order.total_price


# Both ways an order is read: a plain fetch aggregates on demand, the
# list and detail querysets annotate the sum in the same query.
READ_PATHS = [
    pytest.param(lambda order: order, id="fetched"),
    pytest.param(
        lambda order: Order.objects.for_list().get(pk=order.pk),
        id="annotated",
    ),
]


@pytest.mark.parametrize("read", READ_PATHS)
def test_items_refuse_lines_in_different_currencies(read):
    """20 EUR + 10 USD is not 30 of either; the SQL ``SUM`` ignores the
    currency column, so both read paths have to check it."""
    order = _order("0.00", [("10.00", 2)], currency=settings.CURRENCIES[0])
    product = Product.objects.create(
        sku=f"TOTAL-{order.id}-other",
        price=Money(Decimal("1.00"), settings.CURRENCIES[1]),
        stock=1000,
    )
    OrderItem.objects.create(
        order=order,
        product=product,
        price=Money(Decimal("10.00"), settings.CURRENCIES[1]),
        quantity=1,
    )

    with pytest.raises(ValueError, match="different currencies"):
        _ = read(order).total_price_items


@pytest.mark.parametrize("read", READ_PATHS)
def test_items_are_labelled_with_their_own_currency(read):
    """Not the shipping price's: ``total_price`` is where items and
    extras in different currencies are refused."""
    order = _order(
        "5.00",
        [("10.00", 2)],
        currency=settings.CURRENCIES[0],
        item_currency=settings.CURRENCIES[1],
    )

    assert read(order).total_price_items == Money(
        Decimal("20.00"), settings.CURRENCIES[1]
    )


def test_extras_refuse_a_fee_in_another_currency():
    order = _order("5.00")
    order.payment_method_fee = Money(Decimal("1.00"), settings.CURRENCIES[1])

    with pytest.raises(ValueError, match="different currencies"):
        _ = order.total_price_extra


def test_extras_include_the_payment_method_fee():
    order = _order("5.00")
    order.payment_method_fee = Money(Decimal("1.50"), "EUR")

    assert order.total_price_extra == Money(Decimal("6.50"), "EUR")
