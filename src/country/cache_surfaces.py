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
        # ``?shippable=true`` is answered from the store's zones and
        # their active rates (``ShippingService.shippable_country_codes``
        # for countries, ``shipping.zones.HomeCoverage`` for regions), so
        # a rate, a zone or a zone's region set changes this surface's own
        # responses, not just ``shipping``. A zone's region set changes
        # through its M2M, which sends no ``post_save``:
        # ``shipping.signals.zone_regions_changed`` marks it.
        invalidated_by=(
            "shipping.ShippingProvider",
            "shipping.ShippingRate",
            "shipping.ShippingZone",
            "shipping.ShippingZoneRegion",
        ),
        icon="public",
        group="commerce",
    ),
)
