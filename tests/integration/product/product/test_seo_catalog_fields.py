"""GTIN, MPN, the feed title and the category footer text on the API and
in the admin."""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.admin import ProductAdmin, ProductCategoryAdmin
from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from product.serializers.category import ProductCategorySerializer
from product.serializers.product import (
    ProductDetailSerializer,
    ProductRetrieveSerializer,
    ProductSerializer,
    ProductWriteSerializer,
)
from tests.utils.store_settings import switch_every_feature_on
from user.factories import UserAccountFactory

pytestmark = pytest.mark.django_db

VALID_GTIN = "4006381333931"


def _fieldset_fields(admin_class):
    return [
        field
        for _title, options in admin_class.fieldsets
        for field in options["fields"]
    ]


@pytest.mark.parametrize(
    "serializer",
    [ProductSerializer, ProductDetailSerializer, ProductRetrieveSerializer],
)
def test_read_serializers_expose_gtin_and_mpn(serializer):
    product = ProductFactory(
        gtin=VALID_GTIN, mpn="MX-100", num_images=0, num_reviews=0
    )

    data = serializer(product).data

    assert data["gtin"] == VALID_GTIN
    assert data["mpn"] == "MX-100"
    assert serializer().fields["gtin"].read_only
    assert serializer().fields["mpn"].read_only


def test_the_product_detail_endpoint_carries_them_in_camel_case():
    product = ProductFactory(
        gtin=VALID_GTIN, mpn="MX-100", active=True, num_images=0, num_reviews=0
    )

    response = APIClient().get(
        reverse("product-detail", args=[product.pk]), HTTP_ACCEPT_LANGUAGE="el"
    )

    assert response.status_code == status.HTTP_200_OK
    assert response.data["gtin"] == VALID_GTIN
    assert response.data["mpn"] == "MX-100"


def test_feed_title_arrives_through_translations():
    product = ProductFactory(num_images=0, num_reviews=0)
    product.set_current_language("el")
    product.feed_title = "Τίτλος για feed"
    product.save()

    data = ProductSerializer(product).data

    assert data["translations"]["el"]["feed_title"] == "Τίτλος για feed"


def test_the_write_serializer_takes_gtin_and_mpn_and_checks_the_gtin():
    assert {"gtin", "mpn"} <= set(ProductWriteSerializer.Meta.fields)
    field = ProductWriteSerializer().fields["gtin"]

    assert not field.read_only
    assert field.allow_blank
    with pytest.raises(Exception, match="GTIN"):
        field.run_validation("4006381333932")
    assert field.run_validation("") == ""
    assert field.run_validation(VALID_GTIN) == VALID_GTIN


def test_category_footer_text_arrives_through_translations():
    category = ProductCategoryFactory()
    category.set_current_language("el")
    category.footer_text = "<p>Συχνές ερωτήσεις</p>"
    category.save()

    data = ProductCategorySerializer(category).data

    assert "Συχνές ερωτήσεις" in data["translations"]["el"]["footer_text"]


def test_the_admin_lists_the_new_fields():
    product_fields = _fieldset_fields(ProductAdmin)
    for name in ("gtin", "mpn", "feed_title"):
        assert name in product_fields
    assert "footer_text" in _fieldset_fields(ProductCategoryAdmin)


def test_the_product_and_category_pages_render_the_fields():
    switch_every_feature_on()
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )
    product = ProductFactory(
        gtin=VALID_GTIN, mpn="MX-100", num_images=0, num_reviews=0
    )
    category = ProductCategoryFactory()

    page = client.get(
        reverse("admin:product_product_change", args=[product.pk])
    )
    category_page = client.get(
        reverse("admin:product_productcategory_change", args=[category.pk])
    )

    assert page.status_code == category_page.status_code == 200
    html = page.content.decode()
    assert VALID_GTIN in html
    assert "MX-100" in html
    assert 'name="feed_title"' in html
    assert 'name="footer_text"' in category_page.content.decode()
