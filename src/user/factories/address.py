import importlib
import random

import factory
from django.apps import apps
from django.contrib.auth import get_user_model

from core.enum import FloorChoicesEnum, LocationChoicesEnum
from user.models.address import UserAddress

User = get_user_model()


def get_or_create_user():
    if User.objects.exists():
        user = User.objects.order_by("?").first()
    else:
        user_factory_module = importlib.import_module("user.factories.account")
        user_factory_class = user_factory_module.UserAccountFactory
        user = user_factory_class.create()
    return user


def get_or_create_country():
    """Prefer the seeded GR row; a random pick used to be harmless
    when GR was the only real candidate, but the CY seed migration
    (``country/migrations/0012_seed_iso_countries.py``) made it a genuine,
    always-present alternative with a STRICT 4-digit postal format —
    which this factory's generic ``zipcode`` (a locale-agnostic Faker
    postcode) does not reliably satisfy. A test that cares which
    country it gets already passes one explicitly.
    """
    Country = apps.get_model("country", "Country")
    gr = Country.objects.filter(alpha_2="GR").first()
    if gr is not None:
        return gr
    if Country.objects.exists():
        return Country.objects.order_by("?").first()
    country_factory_module = importlib.import_module("country.factories")
    country_factory_class = country_factory_module.CountryFactory
    return country_factory_class.create()


def get_or_create_region():
    """Prefer a GR region, matching ``get_or_create_country``'s
    deterministic GR default above — ``region`` is picked
    independently of ``country`` here, and since the CY region seed
    migration (``region/migrations/0010_seed_cyprus_regions.py``)
    added real CY-* rows, a purely random pick could hand a GR address
    a Cypriot region, which ``UserAddress.clean()``'s region-belongs-
    to-country check now rejects.
    """
    Region = apps.get_model("region", "Region")
    gr_region = Region.objects.filter(country_id="GR").order_by("?").first()
    if gr_region is not None:
        return gr_region
    if Region.objects.exists():
        return Region.objects.order_by("?").first()
    region_factory_module = importlib.import_module("region.factories")
    region_factory_class = region_factory_module.RegionFactory
    return region_factory_class.create()


class UserAddressFactory(factory.django.DjangoModelFactory):
    user = factory.LazyFunction(get_or_create_user)
    title = factory.Faker(
        "random_element",
        elements=[
            "Home",
            "Work",
            "Office",
            "Billing Address",
            "Shipping Address",
            "Primary",
            "Secondary",
            "Parents' House",
            "Vacation Home",
            "Business",
        ],
    )
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    street = factory.Faker("street_name")
    # 1-3 digits: Faker's ``building_number`` can be five digits, which
    # the address rules reject as a postcode typed into the wrong field.
    street_number = factory.Faker("numerify", text="%##")
    city = factory.Faker("city")
    zipcode = factory.Faker("postcode")
    country = factory.LazyFunction(get_or_create_country)
    region = factory.LazyFunction(get_or_create_region)
    floor = factory.LazyFunction(
        lambda: random.choice([s[0] for s in FloorChoicesEnum.choices])
    )
    location_type = factory.LazyFunction(
        lambda: random.choice([s[0] for s in LocationChoicesEnum.choices])
    )
    phone = factory.Faker("phone_number")
    notes = factory.Faker(
        "random_element",
        elements=[
            "Please ring the doorbell",
            "Leave at front door",
            "Call upon arrival",
            "Use back entrance",
            "Apartment 2B",
            "Second floor",
            "Please deliver during business hours",
            "Contact before delivery",
            "Ring twice",
            "",
        ],
    )
    is_main = factory.Faker("pybool", truth_probability=30)

    class Meta:
        model = UserAddress
        django_get_or_create = ("user", "title")
