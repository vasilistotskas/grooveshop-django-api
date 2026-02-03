"""Unit tests for attribute factories."""

import pytest
from django.conf import settings

from product.factories import (
    AttributeFactory,
    AttributeTranslationFactory,
    AttributeValueFactory,
    AttributeValueTranslationFactory,
    ProductAttributeFactory,
)
from product.models import Attribute, AttributeValue, ProductAttribute

pytestmark = pytest.mark.django_db

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


class TestAttributeFactory:
    """Test AttributeFactory."""

    def test_create_attribute(self):
        """Test creating an attribute with factory."""
        attribute = AttributeFactory.create()

        assert attribute.id is not None
        assert attribute.uuid is not None
        assert isinstance(attribute.active, bool)
        assert attribute.created_at is not None
        assert attribute.updated_at is not None

    def test_attribute_translations(self):
        """Test attribute has translations for all languages."""
        attribute = AttributeFactory.create()

        # Should have translations for all configured languages
        assert attribute.translations.count() == len(available_languages)

        # Check each language has a translation
        for lang in available_languages:
            attribute.set_current_language(lang)
            assert attribute.name is not None
            assert len(attribute.name) > 0

    def test_attribute_translation_factory(self):
        """Test AttributeTranslationFactory."""
        attribute = AttributeFactory.create()
        translation = AttributeTranslationFactory.create(
            master=attribute, language_code="en"
        )

        assert translation.language_code == "en"
        assert translation.name is not None
        assert translation.master == attribute

    def test_create_multiple_attributes(self):
        """Test creating multiple attributes."""
        attributes = AttributeFactory.create_batch(5)

        assert len(attributes) == 5
        assert Attribute.objects.count() >= 5

        # Each should have translations
        for attr in attributes:
            assert attr.translations.count() == len(available_languages)


class TestAttributeValueFactory:
    """Test AttributeValueFactory."""

    def test_create_attribute_value(self):
        """Test creating an attribute value with factory."""
        attribute_value = AttributeValueFactory.create()

        assert attribute_value.id is not None
        assert attribute_value.uuid is not None
        assert attribute_value.attribute is not None
        assert isinstance(attribute_value.active, bool)
        assert attribute_value.created_at is not None
        assert attribute_value.updated_at is not None

    def test_attribute_value_translations(self):
        """Test attribute value has translations for all languages."""
        attribute_value = AttributeValueFactory.create()

        # Should have translations for all configured languages
        assert attribute_value.translations.count() == len(available_languages)

        # Check each language has a translation
        for lang in available_languages:
            attribute_value.set_current_language(lang)
            assert attribute_value.value is not None
            assert len(attribute_value.value) > 0

    def test_attribute_value_with_specific_attribute(self):
        """Test creating attribute value with specific attribute."""
        attribute = AttributeFactory.create()
        attribute_value = AttributeValueFactory.create(attribute=attribute)

        assert attribute_value.attribute == attribute

    def test_attribute_value_translation_factory(self):
        """Test AttributeValueTranslationFactory."""
        attribute_value = AttributeValueFactory.create()
        translation = AttributeValueTranslationFactory.create(
            master=attribute_value, language_code="en"
        )

        assert translation.language_code == "en"
        assert translation.value is not None
        assert translation.master == attribute_value

    def test_create_multiple_attribute_values(self):
        """Test creating multiple attribute values."""
        attribute = AttributeFactory.create()
        values = AttributeValueFactory.create_batch(5, attribute=attribute)

        assert len(values) == 5
        assert AttributeValue.objects.filter(attribute=attribute).count() >= 5

        # Each should have translations
        for val in values:
            assert val.translations.count() == len(available_languages)


class TestProductAttributeFactory:
    """Test ProductAttributeFactory."""

    def test_create_product_attribute(self):
        """Test creating a product attribute with factory."""
        product_attribute = ProductAttributeFactory.create()

        assert product_attribute.id is not None
        assert product_attribute.product is not None
        assert product_attribute.attribute_value is not None
        assert product_attribute.created_at is not None
        assert product_attribute.updated_at is not None

    def test_product_attribute_with_specific_values(self):
        """Test creating product attribute with specific product and value."""
        from product.factories import ProductFactory

        product = ProductFactory.create()
        attribute_value = AttributeValueFactory.create()

        product_attribute = ProductAttributeFactory.create(
            product=product, attribute_value=attribute_value
        )

        assert product_attribute.product == product
        assert product_attribute.attribute_value == attribute_value

    def test_create_multiple_product_attributes(self):
        """Test creating multiple product attributes."""
        from product.factories import ProductFactory

        product = ProductFactory.create()
        attribute = AttributeFactory.create()
        values = AttributeValueFactory.create_batch(3, attribute=attribute)

        product_attributes = [
            ProductAttributeFactory.create(product=product, attribute_value=val)
            for val in values
        ]

        assert len(product_attributes) == 3
        assert ProductAttribute.objects.filter(product=product).count() >= 3

    def test_product_attribute_unique_constraint(self):
        """Test that duplicate product-attribute assignments are prevented."""
        from django.db import IntegrityError

        product_attribute = ProductAttributeFactory.create()

        # Attempting to create duplicate should raise IntegrityError
        with pytest.raises(IntegrityError):
            ProductAttributeFactory.create(
                product=product_attribute.product,
                attribute_value=product_attribute.attribute_value,
            )
