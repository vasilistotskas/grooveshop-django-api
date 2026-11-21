"""The shipping app's cache surfaces, which
``core.cache.surfaces.register_default_surfaces`` registers at boot."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface
from core.cache.surfaces import nuxt_handlers

SURFACES = (
    CacheSurface(
        code="shipping",
        label=_("Shipping options"),
        description=_(
            "Shipping option matrix, locker pickers and the ACS"
            " station map. Purge after toggling a provider, editing"
            " a price, or after a station/locker sync."
        ),
        django_patterns=(
            "*BoxNowLockerViewSet_*",
            "*AcsStationViewSet_*",
            "*ShippingPolicyView_*",
        ),
        nuxt_patterns=nuxt_handlers(
            "shipping.options",
            "shipping.lockers",
            "shipping.acs.nearest",
            "shipping.acs.stations",
            "shipping.freeShippingInfo",
            "shipping.policy",
        ),
        # A rate's price, threshold, weight cap or active flag
        # changes what ``/shipping/options`` and
        # ``/shipping/free-shipping-info`` return; a provider's
        # active/capability flags do too. ``/shipping/policy`` is
        # cached here and also depends on the per-kind gate, which
        # for ACS lockers is the synced ``AcsStation`` rows.
        invalidated_by=(
            "shipping.ShippingRate",
            "shipping.ShippingProvider",
            "shipping_acs.AcsStation",
        ),
        icon="local_shipping",
        group="commerce",
    ),
)
