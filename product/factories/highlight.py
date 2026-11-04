import factory
from django.apps import apps
from django.conf import settings

from devtools.factories import CustomDjangoModelFactory
from product.models.highlight import ProductHighlight

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


class ProductHighlightTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    text = factory.Faker("sentence", nb_words=4)
    master = factory.SubFactory(
        "product.factories.highlight.ProductHighlightFactory"
    )

    class Meta:
        model = apps.get_model("product", "ProductHighlightTranslation")
        django_get_or_create = ("language_code", "master")


class ProductHighlightFactory(CustomDjangoModelFactory):
    auto_translations = False

    product = factory.SubFactory("product.factories.product.ProductFactory")

    class Meta:
        model = ProductHighlight
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            ProductHighlightTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
