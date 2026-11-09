from decimal import Decimal
from unittest.mock import patch

import pytest
from djmoney.money import Money

GIFT_CARD_SETTINGS = {
    "GIFT_CARDS_ENABLED": True,
    "GIFT_CARD_VALIDITY_DAYS": 1825,
    "GIFT_CARD_MIN_AMOUNT": 10,
    "GIFT_CARD_MAX_AMOUNT": 500,
}


@pytest.fixture
def enable_gift_cards():
    def _get(key, default=None):
        return GIFT_CARD_SETTINGS.get(key, default)

    with patch("extra_settings.models.Setting.get", side_effect=_get):
        yield


def _eur(amount: str) -> Money:
    return Money(Decimal(amount), "EUR")


def _order_of(total: str, *, gift_card: str):
    """An order of exactly *total* (one 24% line, no shipping or fee), of
    which *gift_card* was paid by gift card. The factory's random lines
    and shipping made the receipt hold less than a refund of the card
    value on some runs, and #530 rightly refuses that refund."""
    from order.factories.item import OrderItemFactory
    from order.factories.order import OrderFactory
    from order.models.order import Order
    from product.factories.product import ProductFactory
    from vat.factories import VatFactory

    order = OrderFactory(num_order_items=0, gift_card_amount=_eur(gift_card))
    Order.objects.filter(pk=order.pk).update(
        shipping_price=_eur("0.00"), payment_method_fee=_eur("0.00")
    )
    OrderItemFactory(
        order=order,
        product=ProductFactory(vat=VatFactory(value=Decimal(24))),
        price=_eur(total),
        quantity=1,
        is_refunded=False,
    )
    order.refresh_from_db()
    return order


@pytest.fixture
def giftcard_order_settled_with_one_card(db):
    """A EUR 100 order that consumed EUR 60 of a single gift card."""
    from giftcard.factories import GiftCardFactory
    from giftcard.models import GiftCardTransaction, GiftCardTransactionKind

    order = _order_of("100.00", gift_card="60.00")
    card = GiftCardFactory(initial_value=Money(Decimal("60.00"), "EUR"))
    GiftCardTransaction.objects.create(
        gift_card=card,
        kind=GiftCardTransactionKind.REDEEM,
        amount=Decimal("-60.00"),
        order=order,
        description="Redeemed",
    )
    return order, card


@pytest.fixture
def giftcard_order_settled_with_two_cards(db):
    """EUR 40 of a EUR 100 order settled by two cards: EUR 30 and EUR 10."""
    from giftcard.factories import GiftCardFactory
    from giftcard.models import GiftCardTransaction, GiftCardTransactionKind

    order = _order_of("100.00", gift_card="40.00")
    first = GiftCardFactory(initial_value=Money(Decimal("30.00"), "EUR"))
    second = GiftCardFactory(initial_value=Money(Decimal("10.00"), "EUR"))
    for card, taken in ((first, "-30.00"), (second, "-10.00")):
        GiftCardTransaction.objects.create(
            gift_card=card,
            kind=GiftCardTransactionKind.REDEEM,
            amount=Decimal(taken),
            order=order,
            description="Redeemed",
        )
    return order, first, second
