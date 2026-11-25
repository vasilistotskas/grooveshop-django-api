"""A product description with no text is "", never NULL.

The search hit declares ``description`` a string and served a NULL one as
``null`` (#684). The field's empty value is "", so the ORM, the API's
translation serializers and the admin write "", and the column is NOT NULL.
"""

from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from product.factories.product import ProductFactory
from product.models.product import ProductTranslation
from product.serializers.product import ProductWriteSerializer

pytestmark = pytest.mark.django_db


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


def test_the_database_refuses_a_null_description():
    product = ProductFactory(
        translations=[ProductTranslation(language_code="en", name="Plain")]
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        ProductTranslation.objects.filter(master=product).update(
            description=None
        )
