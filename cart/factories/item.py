import importlib

import factory
from django.apps import apps
from faker import Faker

from cart.models import CartItem

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


def get_or_create_cart(is_guest=False):
    from cart.factories.cart import CartFactory  # noqa: PLC0415

    if apps.get_model("cart", "Cart").objects.exists():
        if is_guest:
            cart = (
                apps.get_model("cart", "Cart")
                .objects.filter(user__isnull=True)
                .order_by("?")
                .first()
            )
            if cart:
                return cart
        else:
            cart = (
                apps.get_model("cart", "Cart")
                .objects.filter(user__isnull=False)
                .order_by("?")
                .first()
            )
            if cart:
                return cart

        return apps.get_model("cart", "Cart").objects.order_by("?").first()
    elif is_guest:
        return CartFactory(is_guest=True)
    else:
        return CartFactory()


class CartItemFactory(factory.django.DjangoModelFactory):
    cart = factory.LazyFunction(get_or_create_cart)
    product = factory.LazyFunction(get_or_create_product)
    quantity = factory.Faker("random_int", min=1, max=10)

    class Meta:
        model = CartItem
        django_get_or_create = ("cart", "product")

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        is_guest = kwargs.pop("is_guest", False)
        if is_guest and "cart" not in kwargs:
            kwargs["cart"] = get_or_create_cart(is_guest=True)
        return super()._create(model_class, *args, **kwargs)
