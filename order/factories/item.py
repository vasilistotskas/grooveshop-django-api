import factory
from faker import Faker

from order.models.item import OrderItem

fake = Faker()


class OrderItemFactory(factory.django.DjangoModelFactory):
    order = factory.SubFactory("order.factories.order.OrderFactory")
    product = factory.SubFactory("product.factories.product.ProductFactory")
    price = factory.Faker("pydecimal", left_digits=3, right_digits=2, positive=True)
    quantity = factory.Faker("random_int", min=1, max=10)

    class Meta:
        model = OrderItem
        django_get_or_create = ("order", "product")
