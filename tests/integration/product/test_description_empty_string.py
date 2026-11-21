"""A product description with no text is "", never NULL.

The search hit declares ``description`` a string and served a NULL one as
``null`` (#684). The field's empty value is "" in Django's state, so the
ORM, the API's translation serializers and the admin write "", and the
migration turned every stored NULL into "". The column's NOT NULL
constraint is the next release's.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps

from product.factories.product import ProductFactory
from product.models.product import ProductTranslation
from product.serializers.product import ProductWriteSerializer

pytestmark = pytest.mark.django_db

MIGRATION = "product.migrations.0074_product_description_empty_string"


def test_a_translation_saved_without_a_description_stores_empty():
    product = ProductFactory(
        translations=[ProductTranslation(language_code="en", name="Plain")]
    )

    assert product.translations.get().description == ""


def test_the_api_does_not_accept_a_null_description():
    serializer = ProductWriteSerializer(
        data={"translations": {"en": {"name": "A", "description": None}}},
        partial=True,
    )

    assert not serializer.is_valid()
    assert "description" in str(serializer.errors["translations"])


# Seeds a NULL, which only a nullable column takes: release 2's
# SET NOT NULL removes this test with the backfill it covers.
def test_the_migration_turns_every_null_into_empty():
    product = ProductFactory(
        translations=[
            ProductTranslation(language_code="en", name="A", description="x")
        ]
    )
    other = ProductFactory(
        translations=[ProductTranslation(language_code="en", name="B")]
    )
    ProductTranslation.objects.filter(master=other).update(description=None)

    importlib.import_module(MIGRATION).null_to_empty(apps, None)

    assert other.translations.get().description == ""
    assert product.translations.get().description == "x"
