from unittest.mock import patch

import pytest

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

    with patch("giftcard.services.Setting.get", side_effect=_get):
        yield


@pytest.fixture
def giftcard_order_settled_with_one_card(db):
    """An order that consumed EUR 60 of a single gift card."""
    from decimal import Decimal

    from djmoney.money import Money

    from giftcard.factories import GiftCardFactory
    from giftcard.models import GiftCardTransaction, GiftCardTransactionKind
    from order.factories.order import OrderFactory

    order = OrderFactory(gift_card_amount=Money(Decimal("60.00"), "EUR"))
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
    """EUR 40 of an order settled by two cards: EUR 30 and EUR 10."""
    from decimal import Decimal

    from djmoney.money import Money

    from giftcard.factories import GiftCardFactory
    from giftcard.models import GiftCardTransaction, GiftCardTransactionKind
    from order.factories.order import OrderFactory

    order = OrderFactory(gift_card_amount=Money(Decimal("40.00"), "EUR"))
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
