import importlib

import factory
from django.apps import apps
from django.conf import settings

from devtools.factories import CustomDjangoModelFactory
from product.models.attribute_value import AttributeValue

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


def get_or_create_attribute():
    """Get or create an attribute for the attribute value."""
    if apps.get_model("product", "Attribute").objects.exists():
        return (
            apps.get_model("product", "Attribute").objects.order_by("?").first()
        )
    else:
        attribute_factory_module = importlib.import_module(
            "product.factories.attribute"
        )
        attribute_factory_class = attribute_factory_module.AttributeFactory
        return attribute_factory_class.create()


class AttributeValueTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    value = factory.Faker(
        "random_element",
        elements=[
            # Size values
            "Small",
            "Medium",
            "Large",
            "Extra Large",
            "XXL",
            # Color values
            "Red",
            "Blue",
            "Green",
            "Black",
            "White",
            "Yellow",
            "Orange",
            "Purple",
            "Pink",
            "Gray",
            # Capacity values
            "64GB",
            "128GB",
            "256GB",
            "512GB",
            "1TB",
            "2TB",
            # Material values
            "Cotton",
            "Polyester",
            "Leather",
            "Metal",
            "Plastic",
            "Wood",
            "Glass",
            "Stainless Steel",
            # Generic values
            "Yes",
            "No",
            "Standard",
            "Premium",
            "Basic",
            "Professional",
        ],
    )
    master = factory.SubFactory(
        "product.factories.attribute_value.AttributeValueFactory"
    )

    class Meta:
        model = apps.get_model("product", "AttributeValueTranslation")
        django_get_or_create = ("language_code", "master")


class AttributeValueFactory(CustomDjangoModelFactory):
    auto_translations = False

    attribute = factory.LazyFunction(get_or_create_attribute)
    active = factory.Faker("pybool", truth_probability=90)

    class Meta:
        model = AttributeValue
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            AttributeValueTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
