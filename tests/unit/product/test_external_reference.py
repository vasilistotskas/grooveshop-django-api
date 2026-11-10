"""``Product.external_reference``: blank is NULL, a value is unique."""

import pytest
from django.db import IntegrityError, transaction

from product.factories.product import ProductFactory
from product.models.product import Product

pytestmark = pytest.mark.django_db


def test_a_blank_reference_is_saved_as_null():
    product = ProductFactory(external_reference="")

    product.refresh_from_db()
    assert product.external_reference is None


def test_any_number_of_products_may_have_no_reference():
    ProductFactory(external_reference="")
    ProductFactory(external_reference="")
    ProductFactory(external_reference=None)

    assert Product.objects.filter(external_reference__isnull=True).count() >= 3


def test_a_reference_is_unique():
    ProductFactory(external_reference="woo:12")

    with pytest.raises(IntegrityError), transaction.atomic():
        ProductFactory(external_reference="woo:12")


def test_a_partial_save_keeps_the_reference():
    product = ProductFactory(external_reference="woo:99")

    product.stock = 3
    product.save(update_fields=["stock"])

    product.refresh_from_db()
    assert product.external_reference == "woo:99"
