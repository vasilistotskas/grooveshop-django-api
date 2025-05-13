import importlib
import random

import factory
from django.apps import apps
from djmoney.money import Money
from faker import Faker

from order.models.item import OrderItem

fake = Faker()


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


def get_or_create_order():
    if apps.get_model("order", "Order").objects.exists():
        return apps.get_model("order", "Order").objects.order_by("?").first()
    else:
        order_factory_module = importlib.import_module("order.factories.order")
        order_factory_class = order_factory_module.OrderFactory
        return order_factory_class.create()


class OrderItemFactory(factory.django.DjangoModelFactory):
    order = factory.LazyFunction(get_or_create_order)
    product = factory.LazyFunction(get_or_create_product)
    price = factory.LazyFunction(
        lambda: Money(
            fake.pydecimal(
                left_digits=3,
                right_digits=2,
                min_value=5,
                max_value=99,
                positive=True,
            ),
            "USD",
        )
    )
    quantity = factory.LazyFunction(lambda: random.randint(1, 5))
    original_quantity = factory.SelfAttribute("quantity")
    is_refunded = factory.Faker("boolean", chance_of_getting_true=10)
    refunded_quantity = factory.LazyAttribute(
        lambda o: random.randint(1, o.quantity) if o.is_refunded else 0
    )
    notes = factory.LazyFunction(
        lambda: fake.text(max_nb_chars=100) if random.randint(1, 10) > 7 else ""
    )

    class Meta:
        model = OrderItem
        django_get_or_create = ("order", "product")

    @classmethod
    def create_with_refund(cls, **kwargs):
        item = cls.create(**kwargs)
        quantity = item.quantity
        refund_qty = random.randint(1, quantity)
        item.refunded_quantity = refund_qty
        item.is_refunded = refund_qty == quantity
        item.save()
        return item

    @classmethod
    def create_batch_for_order(
        cls, order, count=None, product_count=None, **kwargs
    ):
        if count is None:
            count = random.randint(1, 5)

        if product_count is None or product_count > count:
            product_count = count

        products = []
        if apps.get_model("product", "Product").objects.exists():
            products = list(
                apps.get_model("product", "Product").objects.order_by("?")[
                    :product_count
                ]
            )

        if len(products) < product_count:
            product_factory_module = importlib.import_module(
                "product.factories.product"
            )
            product_factory_class = product_factory_module.ProductFactory
            for _ in range(product_count - len(products)):
                products.append(product_factory_class.create())

        items = []
        for i in range(count):
            product_index = i % len(products)
            item = cls.create(
                order=order, product=products[product_index], **kwargs
            )
            items.append(item)

        return items
