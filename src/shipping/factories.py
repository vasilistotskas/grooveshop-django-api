"""Factories for the generic shipping abstraction."""

from __future__ import annotations

import factory

from country.factories import CountryFactory
from devtools.factories import CustomDjangoModelFactory
from shipping.enum import ShippingKind
from shipping.models import ShippingProvider, ShippingRate, ShippingZone


class ShippingProviderFactory(CustomDjangoModelFactory):
    """Factory for ShippingProvider rows."""

    auto_translations = False

    code = factory.Sequence(lambda n: f"provider_{n}")
    name = factory.LazyAttribute(lambda obj: f"Provider {obj.code}")
    is_active = True
    supports_home_delivery = True
    supports_pickup_point = False
    live_mode = False
    priority = 0
    metadata = factory.LazyFunction(dict)

    class Meta:
        model = ShippingProvider
        skip_postgeneration_save = True
        django_get_or_create = ("code",)


def whole_country_zone(country) -> ShippingZone:
    """The zone covering the whole of ``country`` (its base zone),
    created when the store has none."""
    zone = ShippingZone.objects.filter(
        country=country, regions__isnull=True
    ).first()
    if zone is None:
        zone = ShippingZone.objects.create(name=country.pk, country=country)
    return zone


class ShippingZoneFactory(CustomDjangoModelFactory):
    """Factory for ShippingZone rows: a country of its own unless one is
    given. Pass ``regions`` (a list) for a zone covering only those;
    without, it covers the whole country.
    """

    auto_translations = False

    name = factory.Sequence(lambda n: f"Zone {n}")
    country = factory.SubFactory(CountryFactory, num_regions=0)

    class Meta:
        model = ShippingZone
        skip_postgeneration_save = True

    @factory.post_generation
    def regions(self, create, extracted, **kwargs):
        if create and extracted:
            self.set_regions(extracted)


class ShippingRateFactory(CustomDjangoModelFactory):
    """Factory for ShippingRate rows.

    A rate needs a destination: a ``zone``, or a ``country``, which puts
    it in that country's whole-country zone (:func:`whole_country_zone`).
    Prefer ``tests.utils.shipping.enable_rate`` in a checkout test that
    just needs SOME working rate; use this factory directly for the
    ``ShippingRate``-specific unit tests themselves.
    """

    auto_translations = False

    class Params:
        country = None

    provider = factory.SubFactory(ShippingProviderFactory)
    zone = factory.LazyAttribute(lambda o: whole_country_zone(o.country))
    kind = ShippingKind.HOME_DELIVERY
    price = "3.00"
    free_shipping_threshold = "50.00"
    max_weight_grams = None
    is_active = True

    class Meta:
        model = ShippingRate
        skip_postgeneration_save = True
        django_get_or_create = ("provider", "zone", "kind")
