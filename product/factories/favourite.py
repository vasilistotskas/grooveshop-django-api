import importlib

import factory
from django.apps import apps
from django.contrib.auth import get_user_model

from product.models.product import ProductFavourite

User = get_user_model()


def get_or_create_user():
    if User.objects.exists():
        user = User.objects.order_by("?").first()
    else:
        user_factory_module = importlib.import_module("user.factories.account")
        user_factory_class = user_factory_module.UserAccountFactory
        user = user_factory_class.create()
    return user


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


class ProductFavouriteFactory(factory.django.DjangoModelFactory):
    user = factory.LazyFunction(get_or_create_user)
    product = factory.LazyFunction(get_or_create_product)

    class Meta:
        model = ProductFavourite
        django_get_or_create = ("user", "product")
