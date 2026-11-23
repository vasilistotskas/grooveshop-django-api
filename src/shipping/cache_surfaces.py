"""The shipping app's cache surfaces, which
``core.cache.surfaces.register_default_surfaces`` registers at boot."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface
from core.cache.surfaces import nuxt_handlers

#: ``GET /shipping/product/<id>``, the product page's "shipping from X":
#: a rate moves it, and so do the product (price, weight, size) and the
#: promotions, so the ``products`` and ``promotions`` surfaces purge it
#: too.
PRODUCT_SHIPPING_VIEW = "*ProductShippingView_*"
PRODUCT_SHIPPING_HANDLER = "shipping.product"

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
            PRODUCT_SHIPPING_VIEW,
        ),
        nuxt_patterns=nuxt_handlers(
            "shipping.options",
            "shipping.lockers",
            "shipping.acs.nearest",
            "shipping.acs.stations",
            "shipping.freeShippingInfo",
            "shipping.policy",
            PRODUCT_SHIPPING_HANDLER,
        ),
        # A rate's price, threshold, weight cap or active flag
        # changes what ``/shipping/options`` and
        # ``/shipping/free-shipping-info`` return; a provider's
        # active/capability flags do too. ``/shipping/policy`` is
        # cached here and also depends on the per-kind gate, which
        # for ACS lockers is the synced ``AcsStation`` rows.
        # The locker and station rows are what the two pickup-point
        # lists serve; a sync's deactivation is an unsignalled
        # ``update`` and marks the surface itself.
        invalidated_by=(
            "shipping.ShippingRate",
            "shipping.ShippingProvider",
            "shipping_acs.AcsStation",
            "shipping_boxnow.BoxNowLocker",
        ),
        icon="local_shipping",
        group="commerce",
    ),
)
