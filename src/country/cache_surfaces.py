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
        # ``shipping.signals.zone_regions_changed`` marks it. A carrier's
        # gate counts too (``shipping.zones.carrier_offers``): ACS offers
        # lockers in a country only while it has synced stations there,
        # and a carrier's credentials on ``Tenant`` decide whether it
        # delivers at all (``shipping.tasks.purge_carrier_gate_surfaces``).
        # The reference rows themselves are public: a platform edit of one
        # purges every store.
        invalidated_by=(
            "country.Country",
            "country.CountryTranslation",
            "region.Region",
            "region.RegionTranslation",
            "shipping.ShippingProvider",
            "shipping.ShippingRate",
            "shipping.ShippingZone",
            "shipping.ShippingZoneRegion",
            "shipping_acs.AcsStation",
        ),
        icon="public",
        group="commerce",
    ),
)
