"""``Attribute.schema_property``: the merchant's statement of which
standard product property (colour, size, ...) an attribute is, so the
storefront can emit structured data without guessing from the name."""

import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from product.enum.attribute import SchemaProperty
from product.factories.attribute import AttributeFactory
from product.factories.attribute_value import AttributeValueFactory
from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from product.factories.product_attribute import ProductAttributeFactory
from product.factories.variant_group import ProductVariantGroupFactory
from product.models.attribute import Attribute
from product.serializers.product_attribute import ProductAttributeSerializer
from user.factories.account import UserAccountFactory
from vat.factories import VatFactory

pytestmark = pytest.mark.django_db


def _product_attribute(schema_property, *, is_variant=False):
    attribute = AttributeFactory(
        active=True, is_variant=is_variant, schema_property=schema_property
    )
    value = AttributeValueFactory(attribute=attribute, active=True)
    return ProductAttributeFactory(
        product=ProductFactory(num_images=0, num_reviews=0),
        attribute_value=value,
    )


def test_product_attribute_serializer_exposes_schema_property():
    colour = _product_attribute(SchemaProperty.COLOR)
    plain = _product_attribute("")

    assert ProductAttributeSerializer(colour).data["schema_property"] == (
        "color"
    )
    assert ProductAttributeSerializer(plain).data["schema_property"] == ""


def test_schema_property_is_read_only_on_product_attributes():
    serializer = ProductAttributeSerializer()

    assert serializer.fields["schema_property"].read_only


def test_variants_axes_carry_schema_property_without_extra_queries():
    category = ProductCategoryFactory()
    vat = VatFactory()
    group = ProductVariantGroupFactory(active=True)

    def build(schema_property):
        attribute = AttributeFactory(
            active=True, is_variant=True, schema_property=schema_property
        )
        product = ProductFactory(
            category=category,
            vat=vat,
            active=True,
            variant_group=group,
            num_images=0,
            num_reviews=0,
        )
        ProductAttributeFactory(
            product=product,
            attribute_value=AttributeValueFactory(
                attribute=attribute, active=True
            ),
        )
        return product

    first = build(SchemaProperty.COLOR)
    url = reverse("product-variants", args=[first.pk])
    client = APIClient()

    with CaptureQueriesContext(connection) as one_axis:
        response = client.get(url)
    assert response.status_code == status.HTTP_200_OK
    assert [a["schema_property"] for a in response.data["axes"]] == ["color"]

    build("")
    with CaptureQueriesContext(connection) as two_axes:
        response = client.get(url)
    assert sorted(a["schema_property"] for a in response.data["axes"]) == [
        "",
        "color",
    ]
    assert len(two_axes) == len(one_axis)


def test_admin_edits_schema_property():
    attribute = AttributeFactory(active=True)
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )
    url = reverse("admin:product_attribute_change", args=[attribute.pk])

    page = client.get(url)

    assert page.status_code == status.HTTP_200_OK
    assert "schema_property" in page.context["adminform"].form.fields
    assert Attribute._meta.get_field("schema_property").blank is True
