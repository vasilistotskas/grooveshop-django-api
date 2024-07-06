import factory
from django.apps import apps
from django.conf import settings
from faker import Faker

from core.factories import CustomDjangoModelFactory
from product.models.category import ProductCategory

fake = Faker()

available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


class ProductCategoryTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Faker("word")
    description = factory.Faker("paragraph")
    master = factory.SubFactory("product.factories.category.ProductCategoryFactory")

    class Meta:
        model = apps.get_model("product", "ProductCategoryTranslation")
        django_get_or_create = ("language_code", "master")


class ProductCategoryFactory(CustomDjangoModelFactory):
    unique_model_fields = [
        ("slug", lambda: fake.slug()),
    ]

    menu_image_one = factory.django.ImageField(
        filename="menu_image_one.jpg",
        color=factory.Faker("color"),
        width=1920,
        height=1080,
    )
    menu_image_two = factory.django.ImageField(
        filename="menu_image_two.jpg",
        color=factory.Faker("color"),
        width=1920,
        height=1080,
    )
    menu_main_banner = factory.django.ImageField(
        filename="menu_main_banner.jpg",
        color=factory.Faker("color"),
        width=1920,
        height=1080,
    )
    parent = None

    class Meta:
        model = ProductCategory
        django_get_or_create = ("slug",)
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            ProductCategoryTranslationFactory(language_code=lang, master=self) for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
