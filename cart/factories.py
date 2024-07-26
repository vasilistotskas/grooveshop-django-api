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
        skip_postgeneration_save = True

    @factory.post_generation
    def num_cart_items(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            CartItemFactory.create_batch(extracted, cart=self)


class CartItemFactory(factory.django.DjangoModelFactory):
    cart = factory.SubFactory(CartFactory)
    product = factory.SubFactory("product.factories.product.ProductFactory")
    quantity = factory.Faker("random_int", min=1, max=10)

    class Meta:
        model = CartItem
        django_get_or_create = ("cart", "product")
