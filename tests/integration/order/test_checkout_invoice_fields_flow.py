"""INVOICE checkout with full company requisites, end to end through
the offline order-creation service: Order columns populated, read-side
serializer exposure, invoice buyer snapshot."""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.invoicing import _buyer_snapshot
from order.serializers.order import OrderSerializer
from order.services import OrderService
from pay_way.factories import PayWayFactory
from product.factories import ProductFactory
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db

BILLING_FIELDS = {
    "document_type": "INVOICE",
    "billing_vat_id": "123456783",
    "billing_country": "GR",
    "billing_company_name": "Example IKE",
    "billing_tax_office": "Α' Αθηνών",
    "billing_activity": "Retail trade",
    "billing_street": "Stadiou",
    "billing_street_number": "5",
    "billing_city": "Piraeus",
    "billing_zipcode": "18531",
}


@pytest.fixture
def checkout():
    user = UserAccountFactory()
    country = CountryFactory()
    cart = CartFactory(user=user)
    cart.items.all().delete()
    product = ProductFactory(
        stock=10,
        price=Money(Decimal(100), "EUR"),
        discount_percent=Decimal(0),
        vat=None,
        active=True,
    )
    CartItemFactory(cart=cart, product=product, quantity=1)
    pay_way = PayWayFactory(
        is_online_payment=False,
        cost=Money(Decimal(0), "EUR"),
        free_threshold=Money(Decimal(0), "EUR"),
    )
    shipping_address = {
        "first_name": "Maria",
        "last_name": "Papadopoulou",
        "email": "maria@example.com",
        "street": "Ermou",
        "street_number": "1",
        "city": "Athens",
        "zipcode": "10563",
        "country_id": country.alpha_2,
        "phone": "+306900000000",
        **BILLING_FIELDS,
    }
    return {
        "user": user,
        "cart": cart,
        "pay_way": pay_way,
        "shipping_address": shipping_address,
    }


class TestInvoiceFieldsFlow:
    def test_billing_fields_snapshot_onto_order(self, checkout):
        order = OrderService.create_order_from_cart_offline(
            cart=checkout["cart"],
            shipping_address=checkout["shipping_address"],
            pay_way=checkout["pay_way"],
            user=checkout["user"],
        )

        order.refresh_from_db()
        assert order.document_type == "INVOICE"
        assert order.billing_vat_id == "123456783"
        assert order.billing_company_name == "Example IKE"
        assert order.billing_tax_office == "Α' Αθηνών"
        assert order.billing_activity == "Retail trade"
        assert order.billing_street == "Stadiou"
        assert order.billing_street_number == "5"
        assert order.billing_city == "Piraeus"
        assert order.billing_zipcode == "18531"

    def test_read_serializer_exposes_billing_block(self, checkout):
        order = OrderService.create_order_from_cart_offline(
            cart=checkout["cart"],
            shipping_address=checkout["shipping_address"],
            pay_way=checkout["pay_way"],
            user=checkout["user"],
        )

        data = OrderSerializer(order).data
        assert data["billing_vat_id"] == "123456783"
        assert data["billing_company_name"] == "Example IKE"
        assert data["billing_tax_office"] == "Α' Αθηνών"

    def test_buyer_snapshot_carries_company_block(self, checkout):
        order = OrderService.create_order_from_cart_offline(
            cart=checkout["cart"],
            shipping_address=checkout["shipping_address"],
            pay_way=checkout["pay_way"],
            user=checkout["user"],
        )

        snapshot = _buyer_snapshot(order)
        assert snapshot["company_name"] == "Example IKE"
        assert snapshot["vat_id"] == "123456783"
        assert snapshot["tax_office"] == "Α' Αθηνών"
        assert snapshot["billing_address_line_1"] == "Stadiou 5"
        assert snapshot["billing_city"] == "Piraeus"

    def test_legacy_row_snapshot_falls_back_to_shipping(self, checkout):
        # Orders created before the billing columns existed have blanks
        # — the snapshot must fall back to the shipping address.
        address = {
            **checkout["shipping_address"],
            "billing_street": "",
            "billing_street_number": "",
            "billing_city": "",
            "billing_zipcode": "",
        }
        order = OrderService.create_order_from_cart_offline(
            cart=checkout["cart"],
            shipping_address=address,
            pay_way=checkout["pay_way"],
            user=checkout["user"],
        )
        # Simulate the pre-migration row.
        order.billing_street = ""
        order.billing_street_number = ""
        order.billing_city = ""
        order.billing_zipcode = ""

        snapshot = _buyer_snapshot(order)
        assert snapshot["billing_address_line_1"] == "Ermou 1"
        assert snapshot["billing_city"] == "Athens"
