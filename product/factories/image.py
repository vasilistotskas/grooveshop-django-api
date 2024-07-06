import factory
from django.apps import apps
from django.conf import settings

from product.models.product import ProductImage

available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


class ProductImageTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    title = factory.Faker("sentence", nb_words=4)
    master = factory.SubFactory("product.factories.image.ProductImageFactory")

    class Meta:
        model = apps.get_model("product", "ProductImageTranslation")
        django_get_or_create = ("language_code", "master")


class ProductImageFactory(factory.django.DjangoModelFactory):
    product = factory.SubFactory("product.factories.product.ProductFactory")
    image = factory.django.ImageField(
        filename="product_image.jpg",
        color=factory.Faker("color"),
        width=1280,
        height=720,
    )
    thumbnail = factory.django.ImageField(
        filename="product_thumbnail.jpg",
        color=factory.Faker("color"),
        width=640,
        height=360,
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

        translations = extracted or [
            ProductImageTranslationFactory(language_code=lang, master=self) for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
