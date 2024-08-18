import importlib

import factory
from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import Count
from faker import Faker
from phonenumber_field.phonenumber import PhoneNumber

from order.enum.document_type_enum import OrderDocumentTypeEnum
from order.enum.status_enum import OrderStatusEnum
from order.factories.item import OrderItemFactory
from order.models.order import Order
from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum

fake = Faker()

User = get_user_model()


def get_or_create_user():
    if User.objects.exists():
        user = User.objects.annotate(num_orders=Count("orders")).order_by("num_orders").first()
    else:
        user_factory_module = importlib.import_module("user.factories.account")
        user_factory_class = getattr(user_factory_module, "UserAccountFactory")
        user = user_factory_class.create()
    return user


def get_or_create_country():
    if apps.get_model("country", "Country").objects.exists():
        return apps.get_model("country", "Country").objects.order_by("?").first()
    else:
        country_factory_module = importlib.import_module("country.factories")
        country_factory_class = getattr(country_factory_module, "CountryFactory")
        return country_factory_class.create()


def get_or_create_region():
    if apps.get_model("region", "Region").objects.exists():
        return apps.get_model("region", "Region").objects.order_by("?").first()
    else:
        region_factory_module = importlib.import_module("region.factories")
        region_factory_class = getattr(region_factory_module, "RegionFactory")
        return region_factory_class.create()


def get_or_create_pay_way():
    if apps.get_model("pay_way", "PayWay").objects.exists():
        return apps.get_model("pay_way", "PayWay").objects.order_by("?").first()
    else:
        pay_way_factory_module = importlib.import_module("pay_way.factories")
        pay_way_factory_class = getattr(pay_way_factory_module, "PayWayFactory")
        return pay_way_factory_class.create()


class OrderFactory(factory.django.DjangoModelFactory):
    user = factory.LazyFunction(get_or_create_user)
    country = factory.LazyFunction(get_or_create_country)
    region = factory.LazyFunction(get_or_create_region)
    pay_way = factory.LazyFunction(get_or_create_pay_way)
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
    phone = factory.LazyAttribute(lambda _: PhoneNumber.from_string(fake.phone_number(), region="US"))
    mobile_phone = factory.LazyAttribute(lambda _: PhoneNumber.from_string(fake.phone_number(), region="US"))
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
