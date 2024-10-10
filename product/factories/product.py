import importlib

import factory
from django.apps import apps
from django.conf import settings
from faker import Faker

from core.factories import CustomDjangoModelFactory
from product.factories.image import ProductImageFactory
from product.factories.review import ProductReviewFactory
from product.models.product import Product
from tag.factories.tagged_item import TaggedProductFactory

fake = Faker()

available_languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]


def get_or_create_category():
    if apps.get_model("product", "ProductCategory").objects.exists():
        return apps.get_model("product", "ProductCategory").objects.order_by("?").first()
    else:
        category_factory_module = importlib.import_module("product.factories.category")
        category_factory_class = getattr(category_factory_module, "ProductCategoryFactory")
        return category_factory_class.create()


def get_or_create_vat():
    if apps.get_model("vat", "Vat").objects.exists():
        return apps.get_model("vat", "Vat").objects.order_by("?").first()
    else:
        vat_factory_module = importlib.import_module("vat.factories")
        vat_factory_class = getattr(vat_factory_module, "VatFactory")
        return vat_factory_class.create()


class ProductTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    name = factory.Faker("word")
    description = factory.Faker("paragraph")
    master = factory.SubFactory("product.factories.product.ProductFactory")

    class Meta:
        model = apps.get_model("product", "ProductTranslation")
        django_get_or_create = ("language_code", "master")


class ProductFactory(CustomDjangoModelFactory):
    unique_model_fields = [
        ("slug", lambda: fake.slug()),
    ]

    product_code = factory.Faker("uuid4")
    category = factory.LazyFunction(get_or_create_category)
    price = factory.Faker("pydecimal", left_digits=4, right_digits=2, positive=True)
    active = factory.Faker("boolean")
    stock = factory.Faker("random_int", min=0, max=100)
    discount_percent = factory.Faker(
        "pydecimal", left_digits=2, right_digits=2, positive=True, max_value=100
    )
    vat = factory.LazyFunction(get_or_create_vat)
    view_count = factory.Faker("random_int", min=0, max=1000)
    weight = factory.Faker("pydecimal", left_digits=3, right_digits=2, positive=True)

    class Meta:
        model = Product
        django_get_or_create = ("slug",)
        skip_postgeneration_save = True

    @factory.post_generation
    def num_images(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            ProductImageFactory.create_batch(extracted, product=self)

    @factory.post_generation
    def num_reviews(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            ProductReviewFactory.create_batch(extracted, product=self)

    @factory.post_generation
    def num_tags(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            TaggedProductFactory.create_batch(extracted, content_object=self)

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            ProductTranslationFactory(language_code=lang, master=self) for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
