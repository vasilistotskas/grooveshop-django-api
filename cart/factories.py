import importlib

import factory
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import Count
from django.utils import timezone
from faker import Faker

from cart.models import Cart
from cart.models import CartItem

fake = Faker()

User = get_user_model()


def get_or_create_user():
    if User.objects.exists():
        user = User.objects.annotate(num_carts=Count("cart")).order_by("num_carts").first()
    else:
        user_factory_module = importlib.import_module("user.factories.account")
        user_factory_class = getattr(user_factory_module, "UserAccountFactory")
        user = user_factory_class.create()
    return user


def get_or_create_product():
    if apps.get_model("product", "Product").objects.exists():
        return apps.get_model("product", "Product").objects.order_by("?").first()
    else:
        product_factory_module = importlib.import_module("product.factories.product")
        product_factory_class = getattr(product_factory_module, "ProductFactory")
        return product_factory_class.create()


def get_or_create_cart():
    if apps.get_model("cart", "Cart").objects.exists():
        return apps.get_model("cart", "Cart").objects.order_by("?").first()
    else:
        return factory.SubFactory("cart.factories.CartFactory")


class CartFactory(factory.django.DjangoModelFactory):
    user = factory.LazyFunction(get_or_create_user)
    last_activity = factory.LazyFunction(timezone.now)

    class Meta:
        model = Cart
        django_get_or_create = ("user",)
        skip_postgeneration_save = True

    @factory.post_generation
    def num_cart_items(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            CartItemFactory.create_batch(extracted, cart=self)


class CartItemFactory(factory.django.DjangoModelFactory):
    cart = factory.LazyFunction(get_or_create_cart)
    product = factory.LazyFunction(get_or_create_product)
    quantity = factory.Faker("random_int", min=1, max=10)

    class Meta:
        model = CartItem
        django_get_or_create = ("cart", "product")
