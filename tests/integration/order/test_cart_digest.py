"""The quote's ``cartDigest`` and the order it guards (#755).

``expectedTotal`` refuses an order whose amount due changed since the
quote the buyer was shown. ``expectedCartDigest`` refuses one whose
CONTENTS changed at the same total: a line swapped for one at the same
price, another pickup point. Both are compared under the cart lock by
``OrderService._quote_under_lock`` on both creation paths.

The quote and order creation must take the same inputs, or a digest over
the delivery would refuse every order the quote cannot see the whole of:
``TestTheQuoteTakesWhatTheOrderTakes`` holds every order-creation field
to a declared place.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.urls import reverse
from djmoney.money import Money

from cart.serializers.cart import CartQuoteRequestSerializer
from order.checkout.quote import OrderQuoteService, ShippingChoice
from order.enum.create_error import CART_CHANGED_CODE
from order.models.order import Order
from order.serializers.order import OrderCreateFromCartSerializer
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from promotion.enum import BenefitType, PromotionTrigger, TargetScope
from promotion.factories import PromotionFactory
from promotion.services import PromotionEngine
from region.factories import RegionFactory
from shipping.models import ShippingProvider
from shipping_boxnow.factories import BoxNowLockerFactory
from tests.integration.order.test_checkout_quote import (
    FEE,
    THRESHOLD,
    _address,
    _checkout,
    _choices,
    _post_intent,
    _post_order,
    _product,
    _quote,
    _store_settings,
)
from tests.utils.fake_stripe import fake_stripe
from tests.utils.shipping import enable_rate

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures("checkout_legal_documents"),
]

ORDER_PATH = "/api/v1/order"


def _swap_line(checkout) -> None:
    """Replace the cart's line by another product at the same price: the
    total stays, what the order is made of does not."""
    item = checkout.cart.items.get()
    item.product = _product(checkout.product.price.amount)
    item.save()


def _guards(quote: dict) -> dict:
    return {
        "expected_total": quote["expectedTotal"],
        "expected_cart_digest": quote["cartDigest"],
    }


class TestTheDigest:
    def test_a_chosen_delivery_is_enough(self):
        """The digest needs a delivery, not a pay way: a UCP checkout may
        name its payment method only when it completes (gateway #195)."""
        checkout = _checkout()
        choices = _choices(checkout)
        without_delivery = {
            key: value
            for key, value in choices.items()
            if key not in {"shipping_kind", "country_id"}
        }
        without_pay_way = {
            key: value for key, value in choices.items() if key != "pay_way_id"
        }

        nothing = _quote(checkout, **without_delivery)
        delivery = _quote(checkout, **without_pay_way)
        complete = _quote(checkout, **choices)

        assert nothing["cartDigest"] is None
        assert len(delivery["cartDigest"]) == 64
        int(delivery["cartDigest"], 16)
        assert delivery["expectedTotal"] is None
        assert delivery["cartDigest"] == complete["cartDigest"]

    def test_it_is_stable_while_nothing_changes(self):
        checkout = _checkout()

        first = _quote(checkout, **_choices(checkout))
        second = _quote(checkout, **_choices(checkout))

        assert first["cartDigest"] == second["cartDigest"]

    def test_a_line_at_the_same_price_moves_it_and_not_the_total(self):
        checkout = _checkout()
        before = _quote(checkout, **_choices(checkout))

        _swap_line(checkout)
        after = _quote(checkout, **_choices(checkout))

        assert after["expectedTotal"] == before["expectedTotal"]
        assert after["cartDigest"] != before["cartDigest"]

    def test_quantities_that_trade_off_move_it(self):
        checkout = _checkout(price=Decimal("10.00"))
        other = _product(Decimal("10.00"))
        item = checkout.cart.items.get()
        item.quantity = 2
        item.save()
        checkout.cart.items.create(product=other, quantity=1)
        before = _quote(checkout, **_choices(checkout))

        item.quantity = 1
        item.save()
        checkout.cart.items.filter(product=other).update(quantity=2)
        after = _quote(checkout, **_choices(checkout))

        assert after["expectedTotal"] == before["expectedTotal"]
        assert after["cartDigest"] != before["cartDigest"]

    def test_the_pay_way_is_not_part_of_it(self):
        """What the order is made of does not depend on how it is paid;
        another pay way's fee is ``expectedTotal``'s to catch."""
        checkout = _checkout()
        dearer = PayWayFactory(
            settlement=PaySettlement.COURIER_CASH,
            active=True,
            cost=Money(FEE + Decimal("1.00"), "EUR"),
            free_threshold=Money(THRESHOLD, "EUR"),
        )
        first = _quote(checkout, **_choices(checkout))

        second = _quote(
            checkout, **{**_choices(checkout), "pay_way_id": dearer.id}
        )

        assert second["expectedTotal"] != first["expectedTotal"]
        assert second["cartDigest"] == first["cartDigest"]

    def test_the_line_a_discount_lands_on_moves_it(self):
        checkout = _checkout(price=Decimal("10.00"))
        first = checkout.cart.items.get().product
        second = _product(Decimal("10.00"))
        checkout.cart.items.create(product=second, quantity=1)
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=Decimal("2.00"),
            target_scope=TargetScope.PRODUCTS,
        )
        with _store_settings():
            promotion.products.set([first])
            on_first = _quote(checkout, **_choices(checkout))

            promotion.products.set([second])
            on_second = _quote(checkout, **_choices(checkout))

        assert on_first["discountTotal"] == on_second["discountTotal"] == 2.0
        assert on_first["expectedTotal"] == on_second["expectedTotal"]
        assert on_first["cartDigest"] != on_second["cartDigest"]

    def test_the_carrier_a_home_delivery_is_routed_to_moves_it(self):
        checkout = _checkout()
        resolve = (
            "shipping.services.ShippingService.resolve_home_delivery_provider"
        )

        with patch(resolve, return_value="flat_rate"):
            flat = _quote(checkout, **_choices(checkout))
        with patch(resolve, return_value="acs"):
            promotion = PromotionEngine.evaluate(checkout.cart, user=None)
            acs = OrderQuoteService.cart_digest(
                checkout.cart,
                promotion,
                shipping=ShippingChoice.of(_choices(checkout)),
                weight_grams=0,
            )

        assert flat["cartDigest"] != acs


class TestOrderCreation:
    def test_an_unchanged_cart_is_ordered(self):
        checkout = _checkout()
        quote = _quote(checkout, **_choices(checkout))

        response = _post_order(checkout, **_guards(quote))

        assert response.status_code == 201, response.content

    def test_a_digest_quoted_before_the_pay_way_orders_with_one(self):
        """A UCP complete that names the checkout's first payment method:
        the digest was quoted before any pay way, the total after."""
        checkout = _checkout()
        choices = _choices(checkout)
        digest = _quote(
            checkout,
            **{k: v for k, v in choices.items() if k != "pay_way_id"},
        )["cartDigest"]
        total = _quote(checkout, **choices)["expectedTotal"]
        assert digest is not None

        response = _post_order(
            checkout, expected_total=total, expected_cart_digest=digest
        )

        assert response.status_code == 201, response.content

    def test_a_changed_cart_at_the_same_total_is_409(self, documented):
        checkout = _checkout()
        quote = _quote(checkout, **_choices(checkout))
        _swap_line(checkout)

        response = _post_order(checkout, **_guards(quote))

        assert response.status_code == 409, response.content
        documented(response, ORDER_PATH, "post")
        body = response.json()
        assert body["code"] == CART_CHANGED_CODE
        # ``cart_changed`` is a code until the storefront in production
        # lists it (``order.enum.create_error.CART_CHANGED_CODE``).
        assert body["error"]["type"] == "invalid_order_data"
        fresh = body["error"]["quote"]
        assert fresh["expectedTotal"] == quote["expectedTotal"]
        assert fresh["cartDigest"] != quote["cartDigest"]
        assert (
            fresh["cartDigest"]
            == (_quote(checkout, **_choices(checkout))["cartDigest"])
        )
        assert not Order.objects.exists()
        assert checkout.cart.items.count() == 1

    def test_the_fresh_digest_orders(self):
        checkout = _checkout()
        quote = _quote(checkout, **_choices(checkout))
        _swap_line(checkout)
        refused = _post_order(checkout, **_guards(quote))

        response = _post_order(
            checkout, **_guards(refused.json()["error"]["quote"])
        )

        assert response.status_code == 201, response.content

    def test_a_changed_cart_is_named_before_a_changed_total(self):
        checkout = _checkout()
        quote = _quote(checkout, **_choices(checkout))
        item = checkout.cart.items.get()
        item.product = _product(Decimal("21.00"))
        item.save()

        response = _post_order(checkout, **_guards(quote))

        assert response.status_code == 409, response.content
        assert response.json()["code"] == CART_CHANGED_CODE

    def test_without_a_digest_the_contents_are_not_compared(self):
        checkout = _checkout()
        quote = _quote(checkout, **_choices(checkout))
        _swap_line(checkout)

        response = _post_order(checkout, expected_total=quote["expectedTotal"])

        assert response.status_code == 201, response.content

    def test_the_payment_first_path_compares_it_too(self):
        checkout = _checkout()
        stripe = PayWayFactory(
            provider_code="stripe",
            settlement=PaySettlement.ONLINE,
            active=True,
            cost=Money(FEE, "EUR"),
            free_threshold=Money(THRESHOLD, "EUR"),
        )
        choices = {**_choices(checkout), "pay_way_id": stripe.id}
        with fake_stripe():
            quote = _quote(checkout, **choices)
            intent = _post_intent(checkout, stripe)
            assert intent.status_code == 200, intent.content
            _swap_line(checkout)

            response = _post_order(
                checkout,
                pay_way_id=stripe.id,
                payment_intent_id=intent.data["payment_intent_id"],
                **_guards(quote),
            )

        assert response.status_code == 409, response.content
        assert response.json()["code"] == CART_CHANGED_CODE
        assert not Order.objects.exists()


class TestThePickupPoint:
    """The quote takes the pickup point as order creation does, and the
    digest moves with it: an order goes to the locker it was quoted for.
    """

    @pytest.fixture
    def locker_checkout(self):
        # BoxNow is offered only to a store holding its credentials.
        with patch("shipping_boxnow.services.is_configured", return_value=True):
            yield self._locker_checkout()

    def _locker_checkout(self):
        ShippingProvider.objects.filter(code="boxnow").update(is_active=True)
        checkout = _checkout()
        enable_rate(
            checkout.country, provider_code="boxnow", kind="pickup_point"
        )
        checkout.pay_way = PayWayFactory(
            provider_code="boxnow_pay_on_the_go",
            settlement=PaySettlement.CARRIER_TERMINAL,
            active=True,
        )
        for external_id in ("4", "5"):
            BoxNowLockerFactory(
                external_id=external_id,
                country_code=checkout.country.alpha_2,
            )
        checkout.choices = {
            **_choices(checkout),
            "shipping_kind": "pickup_point",
            "shipping_provider_code": "boxnow",
        }
        return checkout

    def test_another_locker_moves_it_and_not_the_total(self, locker_checkout):
        checkout = locker_checkout

        four = _quote(checkout, **checkout.choices, boxnow_locker_id="4")
        five = _quote(checkout, **checkout.choices, boxnow_locker_id="5")

        assert four["expectedTotal"] == five["expectedTotal"]
        assert four["cartDigest"] != five["cartDigest"]

    def test_the_order_goes_to_the_quoted_locker_only(self, locker_checkout):
        checkout = locker_checkout
        quote = _quote(checkout, **checkout.choices, boxnow_locker_id="4")
        body = {
            "shipping_kind": "pickup_point",
            "shipping_provider_code": "boxnow",
            **_guards(quote),
        }

        elsewhere = _post_order(checkout, boxnow_locker_id="5", **body)
        quoted = _post_order(checkout, boxnow_locker_id="4", **body)

        assert elsewhere.status_code == 409, elsewhere.content
        assert elsewhere.json()["code"] == CART_CHANGED_CODE
        assert quoted.status_code == 201, quoted.content

    def test_a_region_sent_with_a_pickup_order_does_not_change_it(
        self, locker_checkout
    ):
        """A pickup order keeps no region (``_deliver_to_pickup_point``),
        so neither the quote nor the digest reads one: a client that sends
        the buyer's region with both still orders at its quote."""
        checkout = locker_checkout
        region = RegionFactory(country=checkout.country)
        with_region = {
            **checkout.choices,
            "boxnow_locker_id": "4",
            "region_id": region.alpha,
        }
        quote = _quote(checkout, **with_region)
        bare = _quote(
            checkout,
            **{k: v for k, v in with_region.items() if k != "region_id"},
        )

        response = _post_order(
            checkout,
            shipping_kind="pickup_point",
            shipping_provider_code="boxnow",
            boxnow_locker_id="4",
            region_id=region.alpha,
            **_guards(quote),
        )

        assert quote["cartDigest"] == bare["cartDigest"]
        assert response.status_code == 201, response.content


class TestTheShippingChoice:
    def test_a_carrier_reads_its_own_pickup_point(self):
        acs = ShippingChoice.of(
            {
                "shipping_kind": "pickup_point",
                "shipping_provider_code": "acs",
                "country_id": "GR",
                "acs_station_external_id": "ATH01",
                "acs_station_branch": "12",
                # Another carrier's key names nothing ACS ships to.
                "boxnow_locker_id": "4",
            }
        )
        boxnow = ShippingChoice.of(
            {
                "shipping_kind": "pickup_point",
                "shipping_provider_code": "boxnow",
                "country_id": "GR",
                "boxnow_locker_id": "4",
            }
        )

        assert acs.location == (
            ("acs_station_external_id", "ATH01"),
            ("acs_station_branch", "12"),
        )
        assert boxnow.location == (("boxnow_locker_id", "4"),)

    def test_blanks_are_no_choice(self):
        choice = ShippingChoice.of(
            {
                "shipping_kind": "",
                "shipping_provider_code": "",
                "country_id": "GR",
                "region_id": "",
                "boxnow_locker_id": "",
            }
        )

        assert choice == ShippingChoice(
            kind=None, provider_code=None, country_id="GR", region_id=None
        )

    def test_the_digest_moves_with_every_part_of_the_delivery(self):
        checkout = _checkout()
        promotion = PromotionEngine.evaluate(checkout.cart, user=None)
        base = ShippingChoice(
            kind="pickup_point",
            provider_code="acs",
            country_id="GR",
            region_id="I",
            location=(("acs_station_external_id", "ATH01"),),
        )
        variants = [
            ShippingChoice(**{**_fields(base), "kind": "home_delivery"}),
            ShippingChoice(**{**_fields(base), "provider_code": "boxnow"}),
            ShippingChoice(**{**_fields(base), "country_id": "CY"}),
            ShippingChoice(**{**_fields(base), "region_id": "A"}),
            ShippingChoice(
                **{
                    **_fields(base),
                    "location": (("acs_station_external_id", "ATH02"),),
                }
            ),
        ]

        def digest(shipping):
            return OrderQuoteService.cart_digest(
                checkout.cart,
                promotion,
                shipping=shipping,
                weight_grams=0,
            )

        digests = {digest(base), *(digest(v) for v in variants)}
        assert len(digests) == len(variants) + 1

    def test_no_kind_is_a_home_delivery(self):
        checkout = _checkout()
        promotion = PromotionEngine.evaluate(checkout.cart, user=None)

        def digest(kind):
            return OrderQuoteService.cart_digest(
                checkout.cart,
                promotion,
                shipping=ShippingChoice(
                    kind=kind, provider_code=None, country_id="GR"
                ),
                weight_grams=0,
            )

        assert digest(None) == digest("home_delivery")


def _fields(choice: ShippingChoice) -> dict:
    return {
        "kind": choice.kind,
        "provider_code": choice.provider_code,
        "country_id": choice.country_id,
        "region_id": choice.region_id,
        "location": choice.location,
    }


# Every field order creation takes, by what the quote does with it. A
# field added to ``OrderCreateFromCartSerializer`` fails
# ``test_every_order_field_has_a_place`` until it is placed here, and a
# field placed in DIGESTED or PRICED that the quote does not take fails
# ``test_the_quote_takes_every_input_it_prices``.
DIGESTED = {
    # What the order is made of: the cart digest covers it.
    "shipping_kind",
    "shipping_provider_code",
    "country_id",
    "region_id",
    "boxnow_locker_id",
    "acs_station_external_id",
    "acs_station_branch",
}
PRICED = {
    # What it is priced with: the total covers it (``expectedTotal``). The
    # pay way is here, not in the digest: an agent may name it only at
    # completion, and its fee is part of the total.
    "pay_way_id",
    "email",
    "loyalty_points_to_redeem",
    "gift_card_codes",
}
NOT_PRICED = {
    # Who the buyer is and where the parcel goes inside the delivery
    # chosen: neither a price nor a line.
    "first_name",
    "last_name",
    "phone",
    "street",
    "street_number",
    "city",
    "zipcode",
    "floor",
    "location_type",
    "customer_notes",
    # The receipt or invoice the order is documented with.
    "document_type",
    "billing_vat_id",
    "billing_country",
    "billing_company_name",
    "billing_tax_office",
    "billing_activity",
    "billing_street",
    "billing_street_number",
    "billing_city",
    "billing_zipcode",
    # Store-side carrier overrides of the voucher, not a buyer's choice.
    "acs_charge_type",
    "acs_item_quantity",
    # How the order is paid, guarded and recorded.
    "payment_intent_id",
    "expected_total",
    "expected_cart_digest",
    "terms_accepted",
    "agent_checkout",
    "attribution",
    "meta",
}


class TestTheQuoteTakesWhatTheOrderTakes:
    def test_every_order_field_has_a_place(self):
        fields = set(OrderCreateFromCartSerializer().fields)

        assert fields == DIGESTED | PRICED | NOT_PRICED
        assert not DIGESTED & PRICED
        assert not (DIGESTED | PRICED) & NOT_PRICED

    def test_the_quote_takes_every_input_it_prices(self):
        quote_fields = set(CartQuoteRequestSerializer().fields)

        assert DIGESTED | PRICED <= quote_fields

    def test_the_order_body_quotes_as_it_orders(self):
        """The order body itself is a valid quote body (the extra fields
        are ignored) and quotes the digest that order is compared with.
        """
        checkout = _checkout()
        body = {
            **_address(checkout),
            "pay_way_id": checkout.pay_way.id,
            "terms_accepted": True,
        }
        quote = checkout.client.post(
            reverse("cart-quote"),
            body,
            format="json",
            HTTP_X_CART_ID=str(checkout.cart.uuid),
        ).json()

        response = _post_order(checkout, **_guards(quote))

        assert response.status_code == 201, response.content
