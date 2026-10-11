"""``OrderItem.vat_rate``: the VAT a line was sold at, frozen at checkout.

The invoice and the myDATA payload used to back the VAT out of each
line's gross at the product's CURRENT VAT row, days after the sale. A
``Vat`` row edited or deleted in between rewrote a tax document for money
already earned (an order paid at 24% reported at 13%, or at 0% once the
row was gone — ``Product.vat`` is ``SET_NULL``). Every creation path now
writes the rate on the line and the documents read only that.
"""

from __future__ import annotations

import importlib
from decimal import Decimal
from unittest.mock import patch
from xml.etree.ElementTree import fromstring

import pytest
from django.contrib import admin
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import RequestFactory
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.enum.status import PaymentStatus
from order.factories.order import OrderFactory
from order.invoicing import (
    _compute_vat_breakdown,
    _render_items,
    generate_invoice,
)
from order.models.item import OrderItem
from order.mydata.builder import build_invoice_xml
from order.services import OrderService
from pay_way.enum.settlement import PaySettlement
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from promotion.enum import BenefitType, PromotionTrigger
from promotion.factories import PromotionFactory
from tests.utils.shipping import enable_rate
from user.factories import UserAccountFactory
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db

D = Decimal
NS = "{http://www.aade.gr/myDATA/invoice/v1.0}"

_backfill = importlib.import_module(
    "order.migrations.0065_backfill_orderitem_vat_rate"
)


def _product(rate, price="10.00", **kwargs):
    return ProductFactory(
        stock=10,
        price=Money(D(price), "EUR"),
        discount_percent=D(0),
        vat=VatFactory(value=D(rate)) if rate is not None else None,
        active=True,
        **kwargs,
    )


def _cart(*products):
    cart = CartFactory(is_guest=True)
    cart.items.all().delete()
    for product in products:
        CartItemFactory(cart=cart, product=product, quantity=1)
    return cart


def _address(country):
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
        "shipping_kind": "home_delivery",
    }


def _offline_checkout(cart):
    country = CountryFactory()
    enable_rate(country)
    return OrderService.create_order_from_cart_offline(
        cart=cart,
        shipping_address=_address(country),
        pay_way=PayWayFactory(
            settlement=PaySettlement.COURIER_CASH,
            active=True,
            cost=Money(D(0), "EUR"),
            free_threshold=Money(D(0), "EUR"),
        ),
        user=None,
    )


def _payment_first_checkout(cart, user):
    country = CountryFactory()
    enable_rate(country)
    with patch("order.payment.get_payment_provider") as provider:
        provider.return_value.get_payment_status.return_value = (
            PaymentStatus.COMPLETED,
            {"payment_id": "pi_test_123", "status": "succeeded"},
        )
        return OrderService.create_order_from_cart(
            cart=cart,
            shipping_address=_address(country),
            payment_intent_id="pi_test_123",
            pay_way=PayWayFactory(provider_code="stripe"),
            user=user,
        )


def _rates(order):
    return {item.product_id: item.vat_rate for item in order.items.all()}


def _issue(order):
    """The invoice and the myDATA detail lines, as the pipeline builds them."""
    with patch(
        "order.invoicing._render_pdf_bytes", return_value=b"%PDF-1.4 test"
    ):
        invoice = generate_invoice(order)
    built = build_invoice_xml(
        invoice,
        issuer_vat="123456789",
        issuer_country="GR",
        branch=0,
        series_prefix="GRVP",
    )
    details = [
        (
            row.find(f"{NS}vatCategory").text,
            row.find(f"{NS}netValue").text,
            row.find(f"{NS}vatAmount").text,
        )
        for row in fromstring(built.xml_bytes).findall(
            f"{NS}invoice/{NS}invoiceDetails"
        )
    ]
    return invoice, details


class TestSnapshotOnCreation:
    def test_a_line_with_no_stated_rate_takes_its_products_current_vat(self):
        order = OrderFactory(num_order_items=0)
        item = OrderItem.objects.create(
            order=order,
            product=_product("13"),
            price=Money(D("11.30"), "EUR"),
            quantity=1,
        )

        item.refresh_from_db()
        assert item.vat_rate == D("13")

    def test_a_product_with_no_vat_is_snapshotted_at_zero(self):
        order = OrderFactory(num_order_items=0)
        item = OrderItem.objects.create(
            order=order,
            product=_product(None),
            price=Money(D("5.00"), "EUR"),
            quantity=1,
        )

        item.refresh_from_db()
        assert item.vat_rate == D("0")

    def test_a_stated_rate_wins_over_the_products(self):
        order = OrderFactory(num_order_items=0)
        item = OrderItem.objects.create(
            order=order,
            product=_product("24"),
            price=Money(D("11.30"), "EUR"),
            quantity=1,
            vat_rate=D("13"),
        )

        item.refresh_from_db()
        assert item.vat_rate == D("13")

    def test_resaving_a_line_never_re_reads_the_product(self):
        order = OrderFactory(num_order_items=0)
        product = _product("24")
        item = OrderItem.objects.create(
            order=order,
            product=product,
            price=Money(D("12.40"), "EUR"),
            quantity=1,
        )
        product.vat.value = D("13")
        product.vat.save()

        item = OrderItem.objects.get(pk=item.pk)
        item.notes = "gift wrap"
        item.save()

        item.refresh_from_db()
        assert item.vat_rate == D("24")


class TestCheckoutWritesTheRateTheCartUsed:
    def test_the_offline_path_snapshots_each_lines_rate(self):
        standard, reduced, none = _product("24"), _product("6"), _product(None)

        order = _offline_checkout(_cart(standard, reduced, none))

        assert _rates(order) == {
            standard.id: D("24"),
            reduced.id: D("6"),
            none.id: D("0"),
        }

    def test_the_payment_first_path_snapshots_each_lines_rate(self):
        user = UserAccountFactory()
        cart = CartFactory(user=user)
        cart.items.all().delete()
        standard, reduced = _product("24"), _product("13")
        for product in (standard, reduced):
            CartItemFactory(cart=cart, product=product, quantity=1)

        order = _payment_first_checkout(cart, user)

        assert _rates(order) == {standard.id: D("24"), reduced.id: D("13")}

    def test_a_promotion_gift_line_carries_its_products_rate(self):
        gift = _product("13", price="15.00")
        promotion = PromotionFactory(
            trigger=PromotionTrigger.AUTOMATIC,
            benefit_type=BenefitType.FREE_GIFT,
            get_quantity=1,
        )
        promotion.get_products.add(gift)
        paid = _product("24")

        with patch(
            "promotion.services.Setting.get",
            side_effect=lambda key, default=None: {
                "PROMOTIONS_ENABLED": True
            }.get(key, default),
        ):
            order = _offline_checkout(_cart(paid))

        gift_line = order.items.get(product=gift)
        assert gift_line.price.amount == D(0)
        assert gift_line.vat_rate == D("13")
        assert order.items.get(product=paid).vat_rate == D("24")

    def test_editing_the_catalogue_after_checkout_moves_nothing(self):
        product = _product("24")
        order = _offline_checkout(_cart(product))

        product.vat.value = D("13")
        product.vat.save()

        assert _rates(order) == {product.id: D("24")}


class TestIssuedDocumentsIgnoreTheCatalogue:
    """The reproduction from the report: one line, gross 75.34 at 24%."""

    def _order(self):
        product = _product("24")
        order = OrderFactory(
            num_order_items=0, shipping_price=Money(D(0), "EUR")
        )
        order.items.all().delete()
        order.items.create(
            product=product,
            price=Money(D("75.34"), "EUR"),
            quantity=1,
            vat_rate=D("24"),
        )
        return order, product

    def _assert_still_24_percent(self, order):
        breakdown = _compute_vat_breakdown(order)
        assert [
            (D(row["rate"]), row["subtotal"], row["vat"], row["gross"])
            for row in breakdown
        ] == [(D("24"), "60.76", "14.58", "75.34")]

        assert [row["vat_rate"] for row in _render_items(order)] == [D("24")]

        invoice, details = _issue(order)

        assert details == [("1", "60.76", "14.58")]
        assert invoice.total_vat.amount == D("14.58")
        assert invoice.vat_breakdown[0]["vat"] == "14.58"

    def test_at_purchase(self):
        order, _ = self._order()

        self._assert_still_24_percent(order)

    def test_editing_the_vat_row_changes_neither_document(self):
        order, product = self._order()

        product.vat.value = D("13")
        product.vat.save()

        self._assert_still_24_percent(order)

    def test_deleting_the_vat_row_changes_neither_document(self):
        """``Product.vat`` is SET_NULL: the line used to fall into 0%."""
        order, product = self._order()

        product.vat.delete()
        product.refresh_from_db()
        assert product.vat is None

        self._assert_still_24_percent(order)

    def test_moving_the_product_to_another_vat_row_changes_neither(self):
        order, product = self._order()

        product.vat = VatFactory(value=D("6"))
        product.save()

        self._assert_still_24_percent(order)


class TestBackfill:
    """The data step: existing lines take their product's current VAT."""

    @staticmethod
    def _run():
        state = MigrationExecutor(connection).loader.project_state(
            ("order", "0065_backfill_orderitem_vat_rate")
        )
        _backfill.backfill_vat_rate(state.apps, None)

    def _line(self, rate):
        order = OrderFactory(num_order_items=0)
        product = _product(rate)
        item = order.items.create(
            product=product, price=Money(D("10.00"), "EUR"), quantity=1
        )
        # What the line looks like straight after ADD COLUMN: the
        # database default, whatever its product says.
        OrderItem.objects.filter(pk=item.pk).update(vat_rate=D("0"))
        return item

    def test_a_line_takes_its_products_current_vat(self):
        standard, reduced = self._line("24"), self._line("6")

        self._run()

        standard.refresh_from_db()
        reduced.refresh_from_db()
        assert (standard.vat_rate, reduced.vat_rate) == (D("24"), D("6"))

    def test_a_product_with_no_vat_stays_at_zero(self):
        item = self._line(None)

        self._run()

        item.refresh_from_db()
        assert item.vat_rate == D("0")

    def test_running_twice_changes_nothing(self):
        item = self._line("13")

        self._run()
        self._run()

        item.refresh_from_db()
        assert item.vat_rate == D("13")


class TestAdmin:
    def test_a_new_line_cannot_state_a_rate_but_an_existing_one_can_be_fixed(
        self,
    ):
        request = RequestFactory().get("/")
        request.user = UserAccountFactory(admin=True)
        item_admin = admin.site._registry[OrderItem]
        existing = OrderFactory(num_order_items=0).items.create(
            product=_product("24"), price=Money(D("5.00"), "EUR"), quantity=1
        )

        assert "vat_rate" in item_admin.get_readonly_fields(request, None)
        assert "vat_rate" not in item_admin.get_readonly_fields(
            request, existing
        )
