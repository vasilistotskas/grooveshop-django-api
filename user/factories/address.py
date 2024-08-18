import importlib

import factory
from django.apps import apps
from django.contrib.auth import get_user_model

from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum
from user.models.address import UserAddress

User = get_user_model()


def get_or_create_user():
    if User.objects.exists():
        user = User.objects.order_by("?").first()
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


class UserAddressFactory(factory.django.DjangoModelFactory):
    user = factory.LazyFunction(get_or_create_user)
    title = factory.Faker("word")
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    street = factory.Faker("street_name")
    street_number = factory.Faker("building_number")
    city = factory.Faker("city")
    zipcode = factory.Faker("postcode")
    country = factory.LazyFunction(get_or_create_country)
    region = factory.LazyFunction(get_or_create_region)
    floor = factory.Iterator([choice.value for choice in FloorChoicesEnum])
    location_type = factory.Iterator([choice.value for choice in LocationChoicesEnum])
    phone = factory.Faker("phone_number")
    mobile_phone = factory.Faker("phone_number")
    notes = factory.Faker("sentence")
    is_main = factory.Faker("boolean")

    class Meta:
        model = UserAddress
        django_get_or_create = ("user", "title")
