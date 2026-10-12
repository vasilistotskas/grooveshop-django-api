"""A discount lowers the taxable value of the lines it was granted on.

A promotion can be scoped (products, categories, the reward units of a
buy-X-get-Y), so in a basket that mixes VAT rates its money must land on
the lines it covers: a coupon for the 13% food must not be booked against
the 24% line, or the VAT per rate (and in total) on the invoice and the
myDATA payload is wrong. The engine reports the allocation
(``CartDiscountResult.line_discounts``), checkout freezes it on
``OrderItem.discount_allocation``, and ``order.taxation`` reads it; shipping
and the payment fee follow the value of the goods actually charged
(Εγκ. 10/1987).
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

import pytest
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from cart.serializers.cart import CartSerializer
from country.factories import CountryFactory
from loyalty.models.transaction import PointsTransaction
from order.invoicing import generate_invoice
from order.models.item import OrderItem
from order.mydata.builder import build_receipt_xml
from order.services import OrderService
from order.taxation import ChargeKind, order_vat_lines
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductCategoryFactory, ProductFactory
from promotion.enum import BenefitType, PromotionTrigger, TargetScope
from promotion.factories import PromotionFactory
from promotion.services import PromotionEngine
from tests.utils.shipping import enable_rate
from user.factories import UserAccountFactory
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

D = Decimal
NS = "{http://www.aade.gr/myDATA/invoice/v1.0}"
SHIPPING = D("3.00")


@pytest.fixture(autouse=True)
def promotions_and_loyalty():
    values = {
        "PROMOTIONS_ENABLED": True,
        "LOYALTY_ENABLED": True,
        "LOYALTY_REDEMPTION_RATIO_EUR": 100.0,
    }

    def _get(key, default=None):
        return values.get(key, default)

    with (
        patch("promotion.services.Setting.get", side_effect=_get),
        patch("loyalty.services.Setting.get", side_effect=_get),
    ):
        yield


def _product(net, rate, category=None):
    """A product whose NET price is ``net``; the line gross adds the VAT.

    Every product gets a category of its own unless one is given: the
    factory's default is a random existing category, which would put an
    "other" product into the category a promotion is scoped to.
    """
    return ProductFactory(
        stock=100,
        price=Money(D(net), "EUR"),
        discount_percent=D(0),
        vat=VatFactory(value=D(rate)),
        active=True,
        category=category or ProductCategoryFactory(),
    )


def _cart(*lines, user=None):
    """``lines``: ``(product, quantity)``."""
    cart = CartFactory(user=user) if user else CartFactory(is_guest=True)
    cart.items.all().delete()
    for product, quantity in lines:
        CartItemFactory(cart=cart, product=product, quantity=quantity)
    return cart


def _promotion(*, scope=TargetScope.ORDER, targets=(), **kwargs):
    kwargs.setdefault("benefit_type", BenefitType.PERCENTAGE)
    promotion = PromotionFactory(
        trigger=PromotionTrigger.AUTOMATIC,
        target_scope=scope,
        stackable=True,
        **kwargs,
    )
    for target in targets:
        if scope == TargetScope.CATEGORIES:
            promotion.categories.add(target)
        else:
            promotion.products.add(target)
    return promotion


def _checkout(cart, *, user=None, points=None):
    country = CountryFactory()
    enable_rate(country, price=SHIPPING, free_shipping_threshold=None)
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
            "shipping_kind": "home_delivery",
        },
        pay_way=PayWayFactory(
            settlement=PaySettlement.COURIER_CASH,
            active=True,
            cost=Money(D(0), "EUR"),
            free_threshold=Money(D(0), "EUR"),
        ),
        user=user,
        loyalty_points_to_redeem=points,
    )


def _allocation(order):
    return {
        item.product_id: item.discount_allocation.amount
        for item in order.items.all()
    }


def _goods(order):
    """Goods lines as ``(rate, gross)``, in line order."""
    return [
        (line.rate, line.gross)
        for line in order_vat_lines(order)
        if line.kind is ChargeKind.GOODS
    ]


def _shipping(order):
    return [
        (line.rate, line.gross)
        for line in order_vat_lines(order)
        if line.kind is ChargeKind.SHIPPING
    ]


def _documents(order):
    with patch(
        "order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test"
    ):
        invoice = generate_invoice(order)
    built = build_receipt_xml(
        invoice,
        issuer_vat="123456789",
        issuer_country="GR",
        branch=0,
        series_prefix="GRVP",
    )
    return invoice, fromstring(built.xml_bytes)


def _summary(root, name):
    return D(root.find(f"{NS}invoice/{NS}invoiceSummary/{NS}{name}").text)


def _assert_documents_bill_what_the_customer_owes(order):
    order.refresh_from_db()
    invoice, root = _documents(order)

    assert invoice.total.amount == order.amount_due.amount
    assert _summary(root, "totalGrossValue") == order.amount_due.amount
    assert invoice.total_vat.amount == _summary(root, "totalVatAmount")
    assert invoice.subtotal.amount == _summary(root, "totalNetValue")
    return invoice


class TestCategoryScopedCoupon:
    """Food at 13% (net 100.00, gross 113.00) beside other goods at 24%
    (net 100.00, gross 124.00), and a 50% coupon for the food category
    only: 56.50 off, all of it on the food line."""

    def _order(self):
        food_category, other_category = (
            ProductCategoryFactory(),
            ProductCategoryFactory(),
        )
        food = _product("100.00", "13", food_category)
        other = _product("100.00", "24", other_category)
        _promotion(
            scope=TargetScope.CATEGORIES,
            targets=[food_category],
            benefit_value=D(50),
        )
        return _checkout(_cart((food, 1), (other, 1))), food, other

    def test_the_engine_books_the_discount_on_the_food_line_only(self):
        food_category = ProductCategoryFactory()
        food = _product("100.00", "13", food_category)
        other = _product("100.00", "24")
        cart = _cart((food, 1), (other, 1))
        _promotion(
            scope=TargetScope.CATEGORIES,
            targets=[food_category],
            benefit_value=D(50),
        )

        result = PromotionEngine.evaluate(cart)

        by_product = {item.product_id: item.pk for item in cart.items.all()}
        assert result.discount_total.amount == D("56.50")
        assert result.line_discounts == {by_product[food.pk]: D("56.50")}
        assert result.applied[0].line_amounts == result.line_discounts

    def test_checkout_freezes_it_on_the_order_lines(self):
        order, food, other = self._order()

        assert _allocation(order) == {food.pk: D("56.50"), other.pk: D(0)}

    def test_vat_per_rate_is_owed_on_what_each_rate_was_charged(self):
        order, _food, _other = self._order()

        # 56.50 at 13% -> 50.00 + 6.50, 124.00 at 24% -> 100.00 + 24.00.
        # Spread proportionally the 24% line would have been cut by 29.54.
        assert dict(_goods(order)) == {
            D("13"): D("56.50"),
            D("24"): D("124.00"),
        }
        invoice = _assert_documents_bill_what_the_customer_owes(order)
        # The printed table adds each rate's shipping share (2.06 / 0.94).
        rows = {
            D(row["rate"]): (row["subtotal"], row["vat"])
            for row in invoice.vat_breakdown
        }
        assert rows == {
            D("24"): ("101.66", "24.40"),
            D("13"): ("50.83", "6.61"),
        }
        goods_vat = {
            line.rate: line.vat
            for line in order_vat_lines(order)
            if line.kind is ChargeKind.GOODS
        }
        assert goods_vat == {D("13"): D("6.50"), D("24"): D("24.00")}

    def test_shipping_follows_the_value_of_the_goods_charged(self):
        order, _food, _other = self._order()

        # Weights 124.00 (24%) : 56.50 (13%), not the undiscounted
        # 124.00 : 113.00 (which gives 1.57 to the 13% rate).
        assert _shipping(order) == [
            (D("24"), D("2.06")),
            (D("13"), D("0.94")),
        ]

    def test_the_cart_preview_agrees_with_the_invoice(self):
        food_category = ProductCategoryFactory()
        food = _product("100.00", "13", food_category)
        other = _product("100.00", "24")
        cart = _cart((food, 1), (other, 1))
        _promotion(
            scope=TargetScope.CATEGORIES,
            targets=[food_category],
            benefit_value=D(50),
        )

        preview = CartSerializer(cart).data["total_vat_value"]
        order = _checkout(cart)
        goods_vat = sum(
            line.vat
            for line in order_vat_lines(order)
            if line.kind is ChargeKind.GOODS
        )

        assert preview == goods_vat == D("30.50")


class TestProductScopedCoupon:
    def test_a_fixed_amount_for_one_product_leaves_the_others_whole(self):
        books = _product("30.00", "6")
        gadget = _product("70.00", "24")
        _promotion(
            scope=TargetScope.PRODUCTS,
            targets=[books],
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=D("10.00"),
        )

        order = _checkout(_cart((books, 1), (gadget, 1)))

        assert _allocation(order) == {books.pk: D("10.00"), gadget.pk: D(0)}
        assert dict(_goods(order)) == {D("6"): D("21.80"), D("24"): D("86.80")}
        _assert_documents_bill_what_the_customer_owes(order)


class TestBuyXGetY:
    def _order(self):
        cheap = _product("5.00", "6")
        dear = _product("20.00", "24")
        _promotion(
            benefit_type=BenefitType.BXGY,
            benefit_value=D(0),
            buy_quantity=2,
            get_quantity=1,
            get_discount_percent=D(100),
        )
        return _checkout(_cart((cheap, 1), (dear, 2))), cheap, dear

    def test_the_free_unit_is_booked_on_the_line_it_came_from(self):
        order, cheap, dear = self._order()

        assert _allocation(order) == {cheap.pk: D("5.30"), dear.pk: D(0)}

    def test_a_fully_discounted_rate_neither_bills_goods_nor_takes_shipping(
        self,
    ):
        order, _cheap, _dear = self._order()

        assert _goods(order) == [(D("24"), D("49.60"))]
        assert _shipping(order) == [(D("24"), SHIPPING)]
        _assert_documents_bill_what_the_customer_owes(order)


class TestOrderWideAndLoyalty:
    def test_an_order_wide_promotion_and_loyalty_spread_by_value(self):
        user = UserAccountFactory()
        PointsTransaction.objects.create(
            user=user, points=500, transaction_type="EARN"
        )
        standard = _product("100.00", "24")
        reduced = _product("50.00", "13")
        _promotion(benefit_value=D(10))

        order = _checkout(
            _cart((standard, 1), (reduced, 1), user=user),
            user=user,
            points=500,
        )

        # 18.05 promotion over 124.00 : 56.50 = 12.40 / 5.65. 5.00 loyalty
        # over what is left (111.60 : 50.85): 3.43 / 1.57 by largest
        # remainder.
        assert order.loyalty_discount.amount == D("5.00")
        assert _allocation(order) == {
            standard.pk: D("15.83"),
            reduced.pk: D("7.22"),
        }
        _assert_documents_bill_what_the_customer_owes(order)

    def test_the_standalone_redemption_endpoint_allocates_too(self):
        from rest_framework.test import APIClient

        user = UserAccountFactory(num_addresses=0)
        PointsTransaction.objects.create(
            user=user, points=500, transaction_type="EARN"
        )
        order = _checkout(
            _cart((_product("100.00", "24"), 1), (_product("50.00", "13"), 1)),
            user=user,
        )
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            "/api/v1/loyalty/redeem",
            {"points_amount": 500, "currency": "EUR", "order_id": order.id},
            format="json",
        )

        assert response.status_code == 200
        order.refresh_from_db()
        assert sum(_allocation(order).values()) == D("5.00")
        _assert_documents_bill_what_the_customer_owes(order)


class TestStackingScopedAndOrderWide:
    def test_both_land_where_they_apply_and_no_line_goes_below_zero(self):
        food_category = ProductCategoryFactory()
        food = _product("100.00", "13", food_category)
        other = _product("100.00", "24")
        _promotion(
            scope=TargetScope.CATEGORIES,
            targets=[food_category],
            benefit_value=D(50),
            priority=1,
        )
        _promotion(
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=D("30.00"),
            priority=2,
        )

        order = _checkout(_cart((food, 1), (other, 1)))

        # 56.50 on the food, then 30.00 over both by value (113 : 124):
        # 14.30 / 15.70.
        assert order.discount_amount.amount == D("86.50")
        assert _allocation(order) == {food.pk: D("70.80"), other.pk: D("15.70")}
        _assert_documents_bill_what_the_customer_owes(order)

    def test_a_line_the_scoped_promotion_emptied_takes_no_more(self):
        food_category = ProductCategoryFactory()
        food = _product("100.00", "13", food_category)
        other = _product("100.00", "24")
        _promotion(
            scope=TargetScope.CATEGORIES,
            targets=[food_category],
            benefit_value=D(100),
            priority=1,
        )
        _promotion(
            benefit_type=BenefitType.FIXED_AMOUNT,
            benefit_value=D("30.00"),
            priority=2,
        )

        order = _checkout(_cart((food, 1), (other, 1)))

        assert _allocation(order) == {
            food.pk: D("113.00"),
            other.pk: D("30.00"),
        }
        assert order.discount_amount.amount == D("143.00")
        _assert_documents_bill_what_the_customer_owes(order)


class TestCentExactness:
    def test_awkward_amounts_still_sum_to_the_order_total(self):
        category = ProductCategoryFactory()
        lines = [
            (_product("19.99", "24", category), 1),
            (_product("7.77", "13", category), 2),
            (_product("3.33", "6"), 3),
            (_product("1.01", "24", category), 7),
        ]
        _promotion(
            scope=TargetScope.CATEGORIES,
            targets=[category],
            benefit_value=D("33.3"),
        )

        order = _checkout(_cart(*lines))

        assert sum(_allocation(order).values()) == order.discount_amount.amount
        assert all(
            amount == amount.quantize(D("0.01"))
            for amount in _allocation(order).values()
        )
        _assert_documents_bill_what_the_customer_owes(order)


class TestStaffAddedLine:
    def test_it_carries_no_promotion_discount(self):
        category = ProductCategoryFactory()
        food = _product("100.00", "13", category)
        _promotion(
            scope=TargetScope.CATEGORIES,
            targets=[category],
            benefit_value=D(50),
        )
        order = _checkout(_cart((food, 1)))
        extra = OrderItem.objects.create(
            order=order,
            product=_product("10.00", "24"),
            price=Money(D("10.00"), "EUR"),
            quantity=1,
            vat_rate=D("24"),
        )

        gross_by_rate = dict(_goods(order))

        assert extra.discount_allocation.amount == D(0)
        assert gross_by_rate == {D("13"): D("56.50"), D("24"): D("10.00")}


class TestPaymentFirstPath:
    def test_it_freezes_the_allocation_like_the_order_first_path(self):
        from order.enum.status import PaymentStatus

        user = UserAccountFactory()
        category = ProductCategoryFactory()
        food = _product("100.00", "13", category)
        other = _product("100.00", "24")
        _promotion(
            scope=TargetScope.CATEGORIES,
            targets=[category],
            benefit_value=D(50),
        )
        country = CountryFactory()
        enable_rate(country, price=SHIPPING, free_shipping_threshold=None)

        with patch("order.payment.get_payment_provider") as provider:
            provider.return_value.get_payment_status.return_value = (
                PaymentStatus.COMPLETED,
                {"payment_id": "pi_scoped", "status": "succeeded"},
            )
            order = OrderService.create_order_from_cart(
                cart=_cart((food, 1), (other, 1), user=user),
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
                    "shipping_kind": "home_delivery",
                },
                payment_intent_id="pi_scoped",
                pay_way=PayWayFactory(provider_code="stripe"),
                user=user,
            )

        assert _allocation(order) == {food.pk: D("56.50"), other.pk: D(0)}
        _assert_documents_bill_what_the_customer_owes(order)
