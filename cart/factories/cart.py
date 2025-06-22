import importlib
import uuid

import factory
from django.contrib.auth import get_user_model
from django.db.models import Count
from django.utils import timezone
from faker import Faker

from cart.models import Cart

User = get_user_model()

fake = Faker()


def get_or_create_user():
    if User.objects.exists():
        user = (
            User.objects.annotate(num_carts=Count("cart"))
            .order_by("num_carts")
            .first()
        )
    else:
        user_factory_module = importlib.import_module("user.factories.account")
        user_factory_class = user_factory_module.UserAccountFactory
        user = user_factory_class.create()
    return user


class CartFactory(factory.django.DjangoModelFactory):
    user = factory.LazyFunction(get_or_create_user)
    last_activity = factory.LazyFunction(timezone.now)
    session_key = factory.LazyFunction(lambda: str(uuid.uuid4()))

    class Meta:
        model = Cart
        django_get_or_create = ("user",)
        skip_postgeneration_save = True

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        is_guest = kwargs.pop("is_guest", False)
        if is_guest:
            kwargs["user"] = None
            kwargs["session_key"] = kwargs.get("session_key") or fake.uuid4()
        return super()._create(model_class, *args, **kwargs)

    @factory.post_generation
    def num_cart_items(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            from cart.factories.item import CartItemFactory  # noqa: PLC0415

            CartItemFactory.create_batch(extracted, cart=self)
