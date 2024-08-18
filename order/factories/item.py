import importlib

import factory
from django.apps import apps
from faker import Faker

from order.models.item import OrderItem

fake = Faker()


def get_or_create_product():
    if apps.get_model("product", "Product").objects.exists():
        return apps.get_model("product", "Product").objects.order_by("?").first()
    else:
        product_factory_module = importlib.import_module("product.factories.product")
        product_factory_class = getattr(product_factory_module, "ProductFactory")
        return product_factory_class.create()


def get_or_create_order():
    if apps.get_model("order", "Order").objects.exists():
        return apps.get_model("order", "Order").objects.order_by("?").first()
    else:
        order_factory_module = importlib.import_module("order.factories.order")
        order_factory_class = getattr(order_factory_module, "OrderFactory")
        return order_factory_class.create()


class OrderItemFactory(factory.django.DjangoModelFactory):
    order = factory.LazyFunction(get_or_create_order)
    product = factory.LazyFunction(get_or_create_product)
    price = factory.Faker("pydecimal", left_digits=3, right_digits=2, positive=True)
    quantity = factory.Faker("random_int", min=1, max=10)

    class Meta:
        model = OrderItem
        django_get_or_create = ("order", "product")
