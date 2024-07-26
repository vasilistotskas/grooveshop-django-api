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

    @factory.post_generation
    def num_order_items(self, create, extracted, **kwargs):
        if not create:
            return

        if extracted:
            OrderItemFactory.create_batch(extracted, order=self)
