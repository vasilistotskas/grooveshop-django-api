import importlib

import factory
from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db.models import Count

from product.enum.review import RateEnum, ReviewStatus
from product.models.review import ProductReview

available_languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]

User = get_user_model()


def get_or_create_user():
    if User.objects.exists():
        user = (
            User.objects.annotate(num_reviews=Count("product_reviews"))
            .order_by("num_reviews")
            .first()
        )
    else:
        user_factory_module = importlib.import_module("user.factories.account")
        user_factory_class = user_factory_module.UserAccountFactory
        user = user_factory_class.create()
    return user


def get_or_create_product():
    if apps.get_model("product", "Product").objects.exists():
        return (
            apps.get_model("product", "Product")
            .objects.annotate(num_reviews=Count("reviews"))
            .order_by("num_reviews")
            .first()
        )
    else:
        product_factory_module = importlib.import_module(
            "product.factories.product"
        )
        product_factory_class = product_factory_module.ProductFactory
        return product_factory_class.create()


class ProductReviewTranslationFactory(factory.django.DjangoModelFactory):
    language_code = factory.Iterator(available_languages)
    comment = factory.Faker("paragraph")
    master = factory.SubFactory("product.factories.review.ProductReviewFactory")

    class Meta:
        model = apps.get_model("product", "ProductReviewTranslation")
        django_get_or_create = ("language_code", "master")


class ProductReviewFactory(factory.django.DjangoModelFactory):
    product = factory.LazyFunction(get_or_create_product)
    user = factory.LazyFunction(get_or_create_user)
    rate = factory.Iterator([choice.value for choice in RateEnum])
    status = factory.Iterator([choice.value for choice in ReviewStatus])

    class Meta:
        model = ProductReview
        django_get_or_create = ("product", "user")
        skip_postgeneration_save = True

    @factory.post_generation
    def translations(self, create, extracted, **kwargs):
        if not create:
            return

        translations = extracted or [
            ProductReviewTranslationFactory(language_code=lang, master=self)
            for lang in available_languages
        ]

        for translation in translations:
            translation.master = self
            translation.save()
