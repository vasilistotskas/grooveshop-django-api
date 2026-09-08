"""``metadata["payment_type"]`` must describe the pay way that was used.

It was assigned as a literal ``"offline"`` in the metadata dict, while
the ``is_online_payment`` guard a few lines above governed only
``payment_id``. Every card order therefore recorded itself as offline —
78 of them in production, including Viva charges carrying a real
provider payment id.

Nothing consumes the key (myDATA's ``_pick_payment_type`` derives from
the invoice's ``payment_id`` and ``pay_way``), so no logic was wrong.
What was wrong is the audit trail: anyone opening order metadata to work
out how a payment was taken was told "offline" for a card charge.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory

pytestmark = pytest.mark.django_db


def _shipping_address(country):
    return {
        "first_name": "Maria",
        "last_name": "Papadopoulou",
        "email": "maria@example.com",
        "street": "Ermou",
        "street_number": "1",
        "city": "Athens",
        "zipcode": "10563",
        "country_id": country.alpha_2,
        "phone": "+306900000000",
    }


def _cart():
    cart = CartFactory(is_guest=True)
    cart.items.all().delete()
    product = ProductFactory(
        stock=10,
        price=Money(Decimal("10.00"), "EUR"),
        discount_percent=Decimal(0),
        active=True,
    )
    CartItemFactory(cart=cart, product=product, quantity=1)
    return cart


def _pay_way(*, online, provider_code=""):
    return PayWayFactory(
        settlement=(
            PaySettlement.ONLINE if online else PaySettlement.COURIER_CASH
        ),
        provider_code=provider_code,
        active=True,
        cost=Money(Decimal(0), "EUR"),
        free_threshold=Money(Decimal(0), "EUR"),
    )


class TestPaymentTypeMetadataFollowsThePayWay:
    def test_online_redirect_pay_way_records_online(self):
        """The regression: this said "offline" for every card order.

        A REDIRECT provider (Viva) is the case that actually reaches
        this code path. ``create_order_from_cart_offline`` refuses an
        online pay way unless its ``provider_code`` is in
        ``_REDIRECT_PROVIDER_CODES`` — hosted-redirect flows create the
        order first and collect payment afterwards, which is exactly how
        the 78 mislabelled production orders were written.
        """
        order = OrderService.create_order_from_cart_offline(
            cart=_cart(),
            shipping_address=_shipping_address(CountryFactory()),
            pay_way=_pay_way(online=True, provider_code="viva_wallet"),
            user=None,
        )

        assert order.metadata["payment_type"] == "online"

    def test_offline_pay_way_still_records_offline(self):
        """The other half, so the fix is not just a flipped literal."""
        order = OrderService.create_order_from_cart_offline(
            cart=_cart(),
            shipping_address=_shipping_address(CountryFactory()),
            pay_way=_pay_way(online=False),
            user=None,
        )

        assert order.metadata["payment_type"] == "offline"
