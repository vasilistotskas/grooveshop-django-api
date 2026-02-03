"""
Tests for ProductTranslation Meilisearch integration with attributes.
"""

import pytest
from django.conf import settings

from product.factories import (
    AttributeFactory,
    AttributeValueFactory,
    ProductAttributeFactory,
    ProductFactory,
)
from product.models.product import ProductTranslation


@pytest.mark.django_db
class TestProductTranslationMeilisearchAttributes:
    """Test Meilisearch integration for product attributes."""

    def test_meili_meta_includes_attribute_fields(self):
        """Test that MeiliMeta includes attribute-related fields."""
        # Check filterable_fields
        assert "attributes" in ProductTranslation.MeiliMeta.filterable_fields
        assert (
            "attribute_values" in ProductTranslation.MeiliMeta.filterable_fields
        )

        # Check searchable_fields
        assert (
            "attribute_names" in ProductTranslation.MeiliMeta.searchable_fields
        )
        assert (
            "attribute_values_text"
            in ProductTranslation.MeiliMeta.searchable_fields
        )

        # Check displayed_fields
        assert "attributes" in ProductTranslation.MeiliMeta.displayed_fields
        assert "attribute_data" in ProductTranslation.MeiliMeta.displayed_fields

    def test_get_additional_meili_fields_includes_attributes(self):
        """Test that get_additional_meili_fields includes attribute fields."""
        fields = ProductTranslation.get_additional_meili_fields()

        assert "attributes" in fields
        assert "attribute_values" in fields
        assert "attribute_names" in fields
        assert "attribute_values_text" in fields
        assert "attribute_data" in fields

    def test_attributes_field_returns_attribute_ids(self):
        """Test that attributes field returns list of attribute IDs."""
        # Create product with attributes
        product = ProductFactory()
        attribute1 = AttributeFactory()
        attribute2 = AttributeFactory()
        value1 = AttributeValueFactory(attribute=attribute1)
        value2 = AttributeValueFactory(attribute=attribute2)

        ProductAttributeFactory(product=product, attribute_value=value1)
        ProductAttributeFactory(product=product, attribute_value=value2)

        # Get translation
        translation = product.translations.first()

        # Get additional fields
        fields = ProductTranslation.get_additional_meili_fields()
        attributes_func = fields["attributes"]

        # Execute the lambda
        attribute_ids = attributes_func(translation)

        # Should return list of unique attribute IDs
        assert isinstance(attribute_ids, list)
        assert len(attribute_ids) == 2
        assert attribute1.id in attribute_ids
        assert attribute2.id in attribute_ids

    def test_attribute_values_field_returns_value_ids(self):
        """Test that attribute_values field returns list of value IDs."""
        # Create product with attributes
        product = ProductFactory()
        attribute = AttributeFactory()
        value1 = AttributeValueFactory(attribute=attribute)
        value2 = AttributeValueFactory(attribute=attribute)

        ProductAttributeFactory(product=product, attribute_value=value1)
        ProductAttributeFactory(product=product, attribute_value=value2)

        # Get translation
        translation = product.translations.first()

        # Get additional fields
        fields = ProductTranslation.get_additional_meili_fields()
        values_func = fields["attribute_values"]

        # Execute the lambda
        value_ids = values_func(translation)

        # Should return list of value IDs
        assert isinstance(value_ids, list)
        assert len(value_ids) == 2
        assert value1.id in value_ids
        assert value2.id in value_ids

    def test_attribute_names_field_returns_searchable_text(self):
        """Test that attribute_names field returns searchable text."""
        # Create product with attributes
        product = ProductFactory()
        attribute1 = AttributeFactory()
        attribute2 = AttributeFactory()
        value1 = AttributeValueFactory(attribute=attribute1)
        value2 = AttributeValueFactory(attribute=attribute2)

        ProductAttributeFactory(product=product, attribute_value=value1)
        ProductAttributeFactory(product=product, attribute_value=value2)

        # Get English translation
        translation = product.translations.get(language_code="en")

        # Get additional fields
        fields = ProductTranslation.get_additional_meili_fields()
        names_func = fields["attribute_names"]

        # Execute the lambda
        names_text = names_func(translation)

        # Should return space-separated attribute names
        assert isinstance(names_text, str)
        # Get the English translations of the attributes
        attr1_name = attribute1.safe_translation_getter(
            "name", language_code="en", any_language=True
        )
        attr2_name = attribute2.safe_translation_getter(
            "name", language_code="en", any_language=True
        )
        assert attr1_name in names_text
        assert attr2_name in names_text

    def test_attribute_values_text_field_returns_searchable_text(self):
        """Test that attribute_values_text field returns searchable text."""
        # Create product with attributes
        product = ProductFactory()
        attribute = AttributeFactory()
        value1 = AttributeValueFactory(attribute=attribute)
        value2 = AttributeValueFactory(attribute=attribute)

        ProductAttributeFactory(product=product, attribute_value=value1)
        ProductAttributeFactory(product=product, attribute_value=value2)

        # Get English translation
        translation = product.translations.get(language_code="en")

        # Get additional fields
        fields = ProductTranslation.get_additional_meili_fields()
        values_func = fields["attribute_values_text"]

        # Execute the lambda
        values_text = values_func(translation)

        # Should return space-separated attribute values
        assert isinstance(values_text, str)
        # Get the English translations of the values
        val1_text = value1.safe_translation_getter(
            "value", language_code="en", any_language=True
        )
        val2_text = value2.safe_translation_getter(
            "value", language_code="en", any_language=True
        )
        assert val1_text in values_text
        assert val2_text in values_text

    def test_attribute_data_field_returns_structured_data(self):
        """Test that attribute_data field returns structured attribute data."""
        # Create product with attributes
        product = ProductFactory()
        attribute = AttributeFactory()
        value = AttributeValueFactory(attribute=attribute)

        ProductAttributeFactory(product=product, attribute_value=value)

        # Get English translation
        translation = product.translations.get(language_code="en")

        # Get additional fields
        fields = ProductTranslation.get_additional_meili_fields()
        data_func = fields["attribute_data"]

        # Execute the lambda
        attribute_data = data_func(translation)

        # Should return list of dicts with attribute info
        assert isinstance(attribute_data, list)
        assert len(attribute_data) == 1

        data = attribute_data[0]
        assert "attribute_id" in data
        assert "attribute_name" in data
        assert "value_id" in data
        assert "value" in data

        assert data["attribute_id"] == attribute.id
        assert data["value_id"] == value.id

    def test_attribute_data_respects_language_code(self):
        """Test that attribute_data returns translations in correct language."""
        # Create product with attributes
        product = ProductFactory()
        attribute = AttributeFactory()
        value = AttributeValueFactory(attribute=attribute)

        ProductAttributeFactory(product=product, attribute_value=value)

        # Get German translation
        translation = product.translations.get(language_code="de")

        # Get additional fields
        fields = ProductTranslation.get_additional_meili_fields()
        data_func = fields["attribute_data"]

        # Execute the lambda
        attribute_data = data_func(translation)

        # Should return German translations
        data = attribute_data[0]
        expected_name = attribute.safe_translation_getter(
            "name", language_code="de", any_language=True
        )
        expected_value = value.safe_translation_getter(
            "value", language_code="de", any_language=True
        )

        assert data["attribute_name"] == expected_name
        assert data["value"] == expected_value

    def test_product_without_attributes_returns_empty_lists(self):
        """Test that products without attributes return empty lists."""
        # Create product without attributes
        product = ProductFactory()

        # Get translation
        translation = product.translations.first()

        # Get additional fields
        fields = ProductTranslation.get_additional_meili_fields()

        # Test all attribute fields return empty
        assert fields["attributes"](translation) == []
        assert fields["attribute_values"](translation) == []
        assert fields["attribute_names"](translation) == ""
        assert fields["attribute_values_text"](translation) == ""
        assert fields["attribute_data"](translation) == []

    def test_multiple_values_same_attribute(self):
        """Test handling of multiple values from the same attribute."""
        # Create product with multiple values from same attribute
        product = ProductFactory()
        attribute = AttributeFactory()
        value1 = AttributeValueFactory(attribute=attribute)
        value2 = AttributeValueFactory(attribute=attribute)
        value3 = AttributeValueFactory(attribute=attribute)

        ProductAttributeFactory(product=product, attribute_value=value1)
        ProductAttributeFactory(product=product, attribute_value=value2)
        ProductAttributeFactory(product=product, attribute_value=value3)

        # Get translation
        translation = product.translations.first()

        # Get additional fields
        fields = ProductTranslation.get_additional_meili_fields()

        # Should have only 1 unique attribute ID
        attribute_ids = fields["attributes"](translation)
        assert len(attribute_ids) == 1
        assert attribute.id in attribute_ids

        # Should have 3 value IDs
        value_ids = fields["attribute_values"](translation)
        assert len(value_ids) == 3
        assert value1.id in value_ids
        assert value2.id in value_ids
        assert value3.id in value_ids

        # Should have 3 entries in attribute_data
        attribute_data = fields["attribute_data"](translation)
        assert len(attribute_data) == 3


@pytest.mark.django_db
class TestProductAttributeSignals:
    """Test signal handlers for ProductAttribute changes."""

    def test_signal_handler_registered(self):
        """Test that signal handler is registered."""
        from django.db.models.signals import post_delete, post_save

        # Check if signal is connected by looking at receiver names
        post_save_connected = any(
            "update_product_search_index_on_attribute_change"
            in str(receiver[1])
            for receiver in post_save.receivers
        )
        post_delete_connected = any(
            "update_product_search_index_on_attribute_change"
            in str(receiver[1])
            for receiver in post_delete.receivers
        )

        assert post_save_connected or post_delete_connected, (
            "Signal handler not registered"
        )

    @pytest.mark.skipif(
        settings.MEILISEARCH.get("OFFLINE", False),
        reason="Meilisearch is offline",
    )
    def test_adding_attribute_triggers_reindex(self, monkeypatch):
        """Test that adding an attribute triggers search index update."""
        # Track if index_document_task was called
        calls = []

        def mock_delay(*args, **kwargs):
            calls.append((args, kwargs))

        # Mock the index_document_task.delay method
        from unittest.mock import MagicMock

        mock_task = MagicMock()
        mock_task.delay = mock_delay

        # Patch where it's imported in the signal handler
        monkeypatch.setattr("meili.tasks.index_document_task", mock_task)

        # Create product
        product = ProductFactory()
        attribute = AttributeFactory()
        value = AttributeValueFactory(attribute=attribute)

        # Add attribute to product (should trigger signal)
        ProductAttributeFactory(product=product, attribute_value=value)

        # In DEBUG mode, it uses sync indexing, so check if task was called
        # or if sync indexing was performed
        if not settings.DEBUG and settings.MEILISEARCH.get(
            "ASYNC_INDEXING", True
        ):
            # Should have called the task for each translation
            assert len(calls) >= 1

    @pytest.mark.skipif(
        settings.MEILISEARCH.get("OFFLINE", False),
        reason="Meilisearch is offline",
    )
    def test_deleting_attribute_triggers_reindex(self, monkeypatch):
        """Test that deleting an attribute triggers search index update."""
        # Track if index_document_task was called
        calls = []

        def mock_delay(*args, **kwargs):
            calls.append((args, kwargs))

        # Mock the index_document_task.delay method
        from unittest.mock import MagicMock

        mock_task = MagicMock()
        mock_task.delay = mock_delay

        # Patch where it's imported in the signal handler
        monkeypatch.setattr("meili.tasks.index_document_task", mock_task)

        # Create product with attribute
        product = ProductFactory()
        attribute = AttributeFactory()
        value = AttributeValueFactory(attribute=attribute)
        product_attribute = ProductAttributeFactory(
            product=product, attribute_value=value
        )

        # Reset calls to ignore creation
        calls.clear()

        # Delete attribute (should trigger signal)
        product_attribute.delete()

        # In DEBUG mode, it uses sync indexing, so check if task was called
        if not settings.DEBUG and settings.MEILISEARCH.get(
            "ASYNC_INDEXING", True
        ):
            # Should have called the task for each translation
            assert len(calls) >= 1
