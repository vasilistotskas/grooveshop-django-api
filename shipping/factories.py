"""Factories for the generic shipping abstraction."""

from __future__ import annotations

import factory

from devtools.factories import CustomDjangoModelFactory
from shipping.models import ShippingProvider


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
