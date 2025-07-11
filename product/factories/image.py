import importlib

import factory
from django.apps import apps
from django.conf import settings

from product.models.product import ProductImage

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]


def get_or_create_product():
    if apps.get_model("product", "Product").objects.exists():
        return (
            apps.get_model("product", "Product").objects.order_by("?").first()
        )
    else:
        product_factory_module = importlib.import_module(
            "product.factories.product"
        )
        product_factory_class = product_factory_module.ProductFactory
        return product_factory_class.create()


class ProductImageTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    title = factory.Faker("word")
    master = factory.SubFactory("product.factories.image.ProductImageFactory")

    class Meta:
        model = apps.get_model("product", "ProductImageTranslation")
        django_get_or_create = ("language_code", "master")


class ProductImageFactory(factory.django.DjangoModelFactory):
    product = factory.LazyFunction(get_or_create_product)
    image = factory.django.ImageField(
        filename="product_image.jpg",
        color=factory.Faker("color"),
        width=1280,
        height=720,
    )
    is_main = factory.Faker("boolean")

    class Meta:
        model = ProductImage
        django_get_or_create = ("product", "image")
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted is not None:
            if hasattr(self, "translations"):
                self.translations.all().delete()

            translations = extracted
            for translation in translations:
                translation.master = self
                translation.save()
            return

        if hasattr(self, "translations") and self.translations.exists():
            return

        translations = [
            ProductImageTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
