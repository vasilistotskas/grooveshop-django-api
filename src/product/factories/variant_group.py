import factory
from django.apps import apps
from django.conf import settings

from devtools.factories import CustomDjangoModelFactory
from product.models.variant_group import ProductVariantGroup

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


class ProductVariantGroupTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Faker("word")
    master = factory.SubFactory(
        "product.factories.variant_group.ProductVariantGroupFactory"
    )

    class Meta:
        model = apps.get_model("product", "ProductVariantGroupTranslation")
        django_get_or_create = ("language_code", "master")


class ProductVariantGroupFactory(CustomDjangoModelFactory):
    auto_translations = False

    active = factory.Faker("pybool", truth_probability=95)

    class Meta:
        model = ProductVariantGroup
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            ProductVariantGroupTranslationFactory(
                language_code=lang, master=self
            )
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
