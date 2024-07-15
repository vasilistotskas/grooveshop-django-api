import factory
from django.utils import timezone

from cart.models import Cart
from cart.models import CartItem


class CartFactory(factory.django.DjangoModelFactory):
    user = factory.SubFactory("user.factories.account.UserAccountFactory")
    last_activity = factory.LazyFunction(timezone.now)

    class Meta:
        model = Cart
        django_get_or_create = ("user",)
        exclude = ("num_cart_items",)

    num_cart_items = factory.LazyAttribute(lambda o: 3)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        num_cart_items = kwargs.pop("num_items", 3)
        instance = super()._create(model_class, *args, **kwargs)
        if "create" in kwargs and kwargs["create"]:
            if num_cart_items > 0:
                CartItemFactory.create_batch(num_cart_items, cart=instance)
        return instance


class CartItemFactory(factory.django.DjangoModelFactory):
    cart = factory.SubFactory(CartFactory)
    product = factory.SubFactory("product.factories.product.ProductFactory")
    quantity = factory.Faker("random_int", min=1, max=10)

    class Meta:
        model = CartItem
        django_get_or_create = ("cart", "product")
