import factory
from faker import Faker
from phonenumber_field.phonenumber import PhoneNumber

from order.enum.document_type_enum import OrderDocumentTypeEnum
from order.enum.status_enum import OrderStatusEnum
from order.factories.item import OrderItemFactory
from order.models.order import Order
from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum

fake = Faker()


class OrderFactory(factory.django.DjangoModelFactory):
    user = factory.SubFactory("user.factories.account.UserAccountFactory")
    pay_way = factory.SubFactory("pay_way.factories.PayWayFactory")
    country = factory.SubFactory("country.factories.CountryFactory")
    region = factory.SubFactory("region.factories.RegionFactory")
    floor = factory.Iterator(FloorChoicesEnum.choices, getter=lambda x: x[0])
    location_type = factory.Iterator(LocationChoicesEnum.choices, getter=lambda x: x[0])
    email = factory.Faker("email")
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    street = factory.Faker("street_name")
    street_number = factory.Faker("building_number")
    city = factory.Faker("city")
    zipcode = factory.Faker("postcode")
    place = factory.Faker("secondary_address")
    phone = PhoneNumber.from_string(fake.phone_number(), region="US")
    mobile_phone = PhoneNumber.from_string(fake.phone_number(), region="US")
    customer_notes = factory.Faker("text", max_nb_chars=200)
    status = factory.Iterator(OrderStatusEnum.choices, getter=lambda x: x[0])
    shipping_price = factory.Faker("pydecimal", left_digits=2, right_digits=2, positive=True)
    document_type = factory.Iterator(OrderDocumentTypeEnum.choices, getter=lambda x: x[0])
    paid_amount = factory.Faker("pydecimal", left_digits=3, right_digits=2, positive=True)

    class Meta:
        model = Order
        django_get_or_create = ("user",)
        skip_postgeneration_save = True
        exclude = ("num_order_items",)

    num_order_items = factory.LazyAttribute(lambda o: 2)

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        num_order_items = kwargs.pop("num_order_items", 2)
        instance = super()._create(model_class, *args, **kwargs)

        if "create" in kwargs and kwargs["create"]:
            if num_order_items > 0:
                order_items = OrderItemFactory.create_batch(num_order_items)
                instance.items.add(*order_items)

        return instance
