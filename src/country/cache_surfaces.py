"""The country app's cache surfaces, which
``core.cache.surfaces.register_default_surfaces`` registers at boot."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface
from core.cache.surfaces import nuxt_handlers

SURFACES = (
    CacheSurface(
        code="regions_countries",
        label=_("Regions & Countries"),
        description=_(
            "Country and region pickers used during checkout address selection."
        ),
        django_patterns=("*RegionViewSet_*", "*CountryViewSet_*"),
        nuxt_patterns=nuxt_handlers("RegionViewSet", "CountryViewSet"),
        # ``?shippable=true`` is answered from active ShippingRate
        # rows (``ShippingService.shippable_country_codes``), so
        # adding/disabling a rate changes this surface's own
        # ``CountryViewSet`` response, not just ``shipping``.
        invalidated_by=("shipping.ShippingRate",),
        icon="public",
        group="commerce",
    ),
)
