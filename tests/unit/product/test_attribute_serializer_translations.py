"""Default-language translation validation for the Attribute /
AttributeValue serializers (``RequiredDefaultTranslationMixin``).

The rule is settings-driven: the translation for
``settings.PARLER_DEFAULT_LANGUAGE_CODE`` must be present with its
required field; every other locale stays optional.
"""

import pytest
from django.conf import settings

from product.factories.attribute import AttributeFactory
from product.serializers.attribute import AttributeSerializer
from product.serializers.attribute_value import AttributeValueSerializer

DEFAULT_LANG = settings.PARLER_DEFAULT_LANGUAGE_CODE
# A configured non-default language, to prove only the default is required.
OTHER_LANG = next(
    lang["code"]
    for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
    if lang["code"] != DEFAULT_LANG
)


@pytest.mark.django_db
class TestAttributeSerializerTranslations:
    def test_rejects_empty_translations(self):
        serializer = AttributeSerializer(
            data={"active": True, "translations": {}}
        )
        assert not serializer.is_valid()
        assert "translations" in serializer.errors

    def test_rejects_when_only_non_default_language_present(self):
        serializer = AttributeSerializer(
            data={
                "active": True,
                "translations": {OTHER_LANG: {"name": "Color"}},
            }
        )
        assert not serializer.is_valid()
        assert "translations" in serializer.errors

    def test_rejects_default_language_without_name(self):
        serializer = AttributeSerializer(
            data={
                "active": True,
                "translations": {DEFAULT_LANG: {"name": ""}},
            }
        )
        assert not serializer.is_valid()
        assert "translations" in serializer.errors

    def test_accepts_default_language_with_name(self):
        serializer = AttributeSerializer(
            data={
                "active": True,
                "translations": {
                    DEFAULT_LANG: {"name": "Χρώμα"},
                    OTHER_LANG: {"name": "Color"},
                },
            }
        )
        assert serializer.is_valid(), serializer.errors


@pytest.mark.django_db
class TestAttributeValueSerializerTranslations:
    def test_rejects_when_only_non_default_language_present(self):
        attribute = AttributeFactory(active=True)
        serializer = AttributeValueSerializer(
            data={
                "attribute": attribute.id,
                "active": True,
                "translations": {OTHER_LANG: {"value": "Red"}},
            }
        )
        assert not serializer.is_valid()
        assert "translations" in serializer.errors

    def test_accepts_default_language_with_value(self):
        attribute = AttributeFactory(active=True)
        serializer = AttributeValueSerializer(
            data={
                "attribute": attribute.id,
                "active": True,
                "translations": {DEFAULT_LANG: {"value": "Κόκκινο"}},
            }
        )
        assert serializer.is_valid(), serializer.errors
