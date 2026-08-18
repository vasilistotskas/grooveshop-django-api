"""
Tests for ProductTranslation Meilisearch integration with attributes.

Ported from the never-collected ``product/tests/`` tree (pytest
``testpaths`` is ``["tests"]`` only) — this is the only coverage for the
base (non-greeklish) attribute fields on ``get_additional_meili_fields()``
and for the ``update_product_search_index_on_attribute_change`` signal.
See also ``test_product_meili_attribute_shadows.py`` for the greeklish
shadow fields.
"""

from unittest.mock import MagicMock

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
        assert "attributes" in ProductTranslation.MeiliMeta.filterable_fields
        assert (
            "attribute_values" in ProductTranslation.MeiliMeta.filterable_fields
        )

        assert (
            "attribute_names" in ProductTranslation.MeiliMeta.searchable_fields
        )
        assert (
            "attribute_values_text"
            in ProductTranslation.MeiliMeta.searchable_fields
        )

        assert "attributes" in ProductTranslation.MeiliMeta.displayed_fields
        assert "attribute_data" in ProductTranslation.MeiliMeta.displayed_fields

    def test_get_additional_meili_fields_includes_attributes(self):
        fields = ProductTranslation.get_additional_meili_fields()

        assert "attributes" in fields
        assert "attribute_values" in fields
        assert "attribute_names" in fields
        assert "attribute_values_text" in fields
        assert "attribute_data" in fields

    def test_attributes_field_returns_attribute_ids(self):
        product = ProductFactory()
        attribute1 = AttributeFactory()
        attribute2 = AttributeFactory()
        value1 = AttributeValueFactory(attribute=attribute1)
        value2 = AttributeValueFactory(attribute=attribute2)

        ProductAttributeFactory(product=product, attribute_value=value1)
        ProductAttributeFactory(product=product, attribute_value=value2)

        translation = product.translations.first()

        fields = ProductTranslation.get_additional_meili_fields()
        attributes_func = fields["attributes"]

        attribute_ids = attributes_func(translation)

        assert isinstance(attribute_ids, list)
        assert len(attribute_ids) == 2
        assert attribute1.id in attribute_ids
        assert attribute2.id in attribute_ids

    def test_attribute_values_field_returns_value_ids(self):
        product = ProductFactory()
        attribute = AttributeFactory()
        value1 = AttributeValueFactory(attribute=attribute)
        value2 = AttributeValueFactory(attribute=attribute)

        ProductAttributeFactory(product=product, attribute_value=value1)
        ProductAttributeFactory(product=product, attribute_value=value2)

        translation = product.translations.first()

        fields = ProductTranslation.get_additional_meili_fields()
        values_func = fields["attribute_values"]

        value_ids = values_func(translation)

        assert isinstance(value_ids, list)
        assert len(value_ids) == 2
        assert value1.id in value_ids
        assert value2.id in value_ids

    def test_attribute_names_field_returns_searchable_text(self):
        product = ProductFactory()
        attribute1 = AttributeFactory()
        attribute2 = AttributeFactory()
        value1 = AttributeValueFactory(attribute=attribute1)
        value2 = AttributeValueFactory(attribute=attribute2)

        ProductAttributeFactory(product=product, attribute_value=value1)
        ProductAttributeFactory(product=product, attribute_value=value2)

        translation = product.translations.get(language_code="en")

        fields = ProductTranslation.get_additional_meili_fields()
        names_func = fields["attribute_names"]

        names_text = names_func(translation)

        assert isinstance(names_text, str)
        attr1_name = attribute1.safe_translation_getter(
            "name", language_code="en", any_language=True
        )
        attr2_name = attribute2.safe_translation_getter(
            "name", language_code="en", any_language=True
        )
        assert attr1_name in names_text
        assert attr2_name in names_text

    def test_attribute_values_text_field_returns_searchable_text(self):
        product = ProductFactory()
        attribute = AttributeFactory()
        value1 = AttributeValueFactory(attribute=attribute)
        value2 = AttributeValueFactory(attribute=attribute)

        ProductAttributeFactory(product=product, attribute_value=value1)
        ProductAttributeFactory(product=product, attribute_value=value2)

        translation = product.translations.get(language_code="en")

        fields = ProductTranslation.get_additional_meili_fields()
        values_func = fields["attribute_values_text"]

        values_text = values_func(translation)

        assert isinstance(values_text, str)
        val1_text = value1.safe_translation_getter(
            "value", language_code="en", any_language=True
        )
        val2_text = value2.safe_translation_getter(
            "value", language_code="en", any_language=True
        )
        assert val1_text in values_text
        assert val2_text in values_text

    def test_attribute_data_field_returns_structured_data(self):
        product = ProductFactory()
        attribute = AttributeFactory()
        value = AttributeValueFactory(attribute=attribute)

        ProductAttributeFactory(product=product, attribute_value=value)

        translation = product.translations.get(language_code="en")

        fields = ProductTranslation.get_additional_meili_fields()
        data_func = fields["attribute_data"]

        attribute_data = data_func(translation)

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
        product = ProductFactory()
        attribute = AttributeFactory()
        value = AttributeValueFactory(attribute=attribute)

        ProductAttributeFactory(product=product, attribute_value=value)

        translation = product.translations.get(language_code="de")

        fields = ProductTranslation.get_additional_meili_fields()
        data_func = fields["attribute_data"]

        attribute_data = data_func(translation)

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
        product = ProductFactory()

        translation = product.translations.first()

        fields = ProductTranslation.get_additional_meili_fields()

        assert fields["attributes"](translation) == []
        assert fields["attribute_values"](translation) == []
        assert fields["attribute_names"](translation) == ""
        assert fields["attribute_values_text"](translation) == ""
        assert fields["attribute_data"](translation) == []

    def test_multiple_values_same_attribute(self):
        product = ProductFactory()
        attribute = AttributeFactory()
        value1 = AttributeValueFactory(attribute=attribute)
        value2 = AttributeValueFactory(attribute=attribute)
        value3 = AttributeValueFactory(attribute=attribute)

        ProductAttributeFactory(product=product, attribute_value=value1)
        ProductAttributeFactory(product=product, attribute_value=value2)
        ProductAttributeFactory(product=product, attribute_value=value3)

        translation = product.translations.first()

        fields = ProductTranslation.get_additional_meili_fields()

        attribute_ids = fields["attributes"](translation)
        assert len(attribute_ids) == 1
        assert attribute.id in attribute_ids

        value_ids = fields["attribute_values"](translation)
        assert len(value_ids) == 3
        assert value1.id in value_ids
        assert value2.id in value_ids
        assert value3.id in value_ids

        attribute_data = fields["attribute_data"](translation)
        assert len(attribute_data) == 3


@pytest.mark.django_db
class TestProductAttributeSignals:
    """Test signal handlers for ProductAttribute changes."""

    @pytest.mark.skipif(
        settings.MEILISEARCH.get("OFFLINE", False),
        reason="Meilisearch is offline",
    )
    def test_adding_attribute_triggers_reindex(self, monkeypatch):
        calls = []

        def mock_delay(*args, **kwargs):
            calls.append((args, kwargs))

        mock_task = MagicMock()
        mock_task.delay = mock_delay

        monkeypatch.setattr("meili.tasks.index_document_task", mock_task)

        product = ProductFactory()
        attribute = AttributeFactory()
        value = AttributeValueFactory(attribute=attribute)

        ProductAttributeFactory(product=product, attribute_value=value)

        if not settings.DEBUG and settings.MEILISEARCH.get(
            "ASYNC_INDEXING", True
        ):
            assert len(calls) >= 1

    @pytest.mark.skipif(
        settings.MEILISEARCH.get("OFFLINE", False),
        reason="Meilisearch is offline",
    )
    def test_deleting_attribute_triggers_reindex(self, monkeypatch):
        calls = []

        def mock_delay(*args, **kwargs):
            calls.append((args, kwargs))

        mock_task = MagicMock()
        mock_task.delay = mock_delay

        monkeypatch.setattr("meili.tasks.index_document_task", mock_task)

        product = ProductFactory()
        attribute = AttributeFactory()
        value = AttributeValueFactory(attribute=attribute)
        product_attribute = ProductAttributeFactory(
            product=product, attribute_value=value
        )

        calls.clear()

        product_attribute.delete()

        if not settings.DEBUG and settings.MEILISEARCH.get(
            "ASYNC_INDEXING", True
        ):
            assert len(calls) >= 1
