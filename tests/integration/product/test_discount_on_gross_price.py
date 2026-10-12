"""The markdown takes its percent off what the shopper pays.

It reduces the NET price and VAT is charged on the reduced net, so
``final_price == price * (1 - p) * (1 + vat)``: 30.00 net at 24% VAT with
10% off costs 33.48, not 34.20 (VAT on the undiscounted net). Every
consumer reads the one definition; the SQL twins must agree with it.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from djmoney.money import Money

from product.factories import ProductFactory
from product.models.product import Product
from vat.models import Vat


@pytest.fixture
def vat(db):
    return Vat.objects.get_or_create(value=Decimal("24.0"))[0]


def make(vat, price="30.00", discount="10"):
    return ProductFactory(
        price=Decimal(price), discount_percent=Decimal(discount), vat=vat
    )


@pytest.mark.django_db
class TestFormula:
    def test_ten_percent_off_takes_ten_percent_off_the_gross(self, vat):
        product = make(vat)

        assert product.final_price == Money("33.48", "EUR")
        assert product.final_price.amount == Decimal("37.20") * Decimal("0.9")

    def test_the_parts_add_up(self, vat):
        product = make(vat)

        assert product.discount_value == Money("3.00", "EUR")
        assert product.vat_value == Money("6.48", "EUR")  # 27.00 x 24%
        assert (
            product.price.amount
            - product.discount_value.amount
            + product.vat_value.amount
            == product.final_price.amount
        )

    def test_no_markdown_is_unchanged(self, vat):
        product = make(vat, discount="0")

        assert product.final_price == Money("37.20", "EUR")
        assert product.vat_value == Money("7.20", "EUR")

    def test_no_vat_rate_is_unchanged(self, db):
        product = ProductFactory(
            price=Decimal("30.00"), discount_percent=Decimal(10), vat=None
        )

        assert product.final_price == Money("27.00", "EUR")
        assert product.vat_value == Money("0", "EUR")

    def test_sql_annotations_agree_with_the_properties(self, vat):
        product = make(vat)

        annotated = Product.objects.with_final_price_annotation().get(
            pk=product.pk
        )

        assert annotated.final_price_annotation == Decimal("33.48")
        assert annotated.vat_value_annotation == Decimal("6.48")
        assert annotated.discount_value_annotation == Decimal("3.00")

    def test_the_indexed_price_is_the_same_figure(self, vat):
        from product.models.product import ProductTranslation

        product = make(vat)
        translation = ProductTranslation.get_meilisearch_queryset().get(
            master=product, language_code="en"
        )

        assert translation.meili_serialize()["final_price"] == 33.48

    def test_the_price_filter_uses_the_charged_price(self, vat):
        from product.filters.product import ProductFilter

        product = make(vat)

        found = ProductFilter(
            {"min_final_price": "33.48", "max_final_price": "33.48"},
            queryset=Product.objects.all(),
        ).qs

        assert product in found
