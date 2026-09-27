"""Factories for the generic shipping abstraction."""

from __future__ import annotations

import factory

from devtools.factories import CustomDjangoModelFactory
from shipping.enum import ShippingKind
from shipping.models import ShippingProvider, ShippingRate


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


class ShippingRateFactory(CustomDjangoModelFactory):
    """Factory for ShippingRate rows.

    ``country`` has no default — every test must say which country
    this rate covers, the entire point of the model. Prefer
    ``tests.utils.shipping.enable_rate`` in a checkout test that just
    needs SOME working rate; use this factory directly for the
    ``ShippingRate``-specific unit tests themselves.
    """

    auto_translations = False

    provider = factory.SubFactory(ShippingProviderFactory)
    kind = ShippingKind.HOME_DELIVERY
    price = "3.00"
    free_shipping_threshold = "50.00"
    max_weight_grams = None
    is_active = True

    class Meta:
        model = ShippingRate
        skip_postgeneration_save = True
        django_get_or_create = ("provider", "country", "kind")
