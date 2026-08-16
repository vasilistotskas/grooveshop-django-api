"""Greeklish shadows for the product attribute search attributes.

Greek attribute values ("Πλαστικό", "Μπλε") frequently exist ONLY in
``attribute_values_text`` — never in the product name — so without these
shadows greeklish queries like "plastiko" or "ble" cannot match the
product at all.
"""

import pytest

from product.factories.attribute import AttributeFactory
from product.factories.attribute_value import AttributeValueFactory
from product.factories.product import ProductFactory
from product.factories.product_attribute import ProductAttributeFactory
from product.models.product import ProductTranslation


def _set_el(instance, field, value):
    instance.set_current_language("el")
    setattr(instance, field, value)
    instance.save()


@pytest.fixture
def translation_with_greek_attributes(db):
    product = ProductFactory()
    material = AttributeFactory()
    _set_el(material, "name", "Υλικό")
    plastic = AttributeValueFactory(attribute=material)
    _set_el(plastic, "value", "Πλαστικό")
    ProductAttributeFactory(product=product, attribute_value=plastic)

    color = AttributeFactory()
    _set_el(color, "name", "Χρώμα")
    blue = AttributeValueFactory(attribute=color)
    _set_el(blue, "value", "Μπλε")
    ProductAttributeFactory(product=product, attribute_value=blue)

    return product.translations.get(language_code="el")


@pytest.mark.django_db
class TestAttributeGreeklishShadows:
    def test_attribute_names_have_greeklish_shadow(
        self, translation_with_greek_attributes
    ):
        fields = ProductTranslation.get_additional_meili_fields()
        names = fields["attribute_names"](translation_with_greek_attributes)
        assert set(names.split()) == {"Υλικό", "Χρώμα"}
        shadow = fields["attribute_names_greeklish"](
            translation_with_greek_attributes
        )
        assert set(shadow.split()) == {"uliko", "xroma"}

    def test_attribute_values_have_greeklish_and_alt_shadows(
        self, translation_with_greek_attributes
    ):
        fields = ProductTranslation.get_additional_meili_fields()
        values = fields["attribute_values_text"](
            translation_with_greek_attributes
        )
        assert set(values.split()) == {"Πλαστικό", "Μπλε"}
        shadow = fields["attribute_values_text_greeklish"](
            translation_with_greek_attributes
        )
        assert set(shadow.split()) == {"plastiko", "mple"}
        # Μπλε starts with the μπ digraph, so the alt fold differs and
        # must be indexed ("ble" is what users type for the /b/ sound).
        alt = fields["attribute_values_text_greeklish_alt"](
            translation_with_greek_attributes
        )
        assert set(alt.split()) == {"plastiko", "ble"}

    def test_alt_shadow_is_none_without_word_initial_digraph(self, db):
        product = ProductFactory()
        size = AttributeFactory()
        _set_el(size, "name", "Μέγεθος")
        large = AttributeValueFactory(attribute=size)
        _set_el(large, "value", "Μεγάλο")
        ProductAttributeFactory(product=product, attribute_value=large)
        translation = product.translations.get(language_code="el")

        fields = ProductTranslation.get_additional_meili_fields()
        assert (
            fields["attribute_values_text_greeklish"](translation) == "megalo"
        )
        assert (
            fields["attribute_values_text_greeklish_alt"](translation) is None
        )

    def test_shadows_are_none_for_product_without_attributes(self, db):
        product = ProductFactory()
        translation = product.translations.get(language_code="el")
        fields = ProductTranslation.get_additional_meili_fields()
        assert fields["attribute_names_greeklish"](translation) is None
        assert fields["attribute_values_text_greeklish"](translation) is None
        assert (
            fields["attribute_values_text_greeklish_alt"](translation) is None
        )


    def test_attribute_fields_have_full_variant_bags(
        self, translation_with_greek_attributes
    ):
        fields = ProductTranslation.get_additional_meili_fields()
        names_bag = set(
            fields["attribute_names_greeklish_variants"](
                translation_with_greek_attributes
            ).split()
        )
        # υλικό: all three first-letter conventions (y/u/i) exactly
        assert {"yliko", "uliko", "iliko"} <= names_bag
        # χρώμα: the x- and h- conventions typo tolerance cannot reach
        assert {"xroma", "hroma"} <= names_bag

        values_bag = set(
            fields["attribute_values_text_greeklish_variants"](
                translation_with_greek_attributes
            ).split()
        )
        assert {"mple", "ble"} <= values_bag
        assert "plastiko" in values_bag
