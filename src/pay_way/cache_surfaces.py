"""The pay_way app's cache surfaces, which
``core.cache.surfaces.register_default_surfaces`` registers at boot."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from core.cache.registry import CacheSurface
from core.cache.surfaces import nuxt_handlers

SURFACES = (
    CacheSurface(
        code="pay_way",
        label=_("Payment methods"),
        description=_(
            "Payment-method list and detail responses, plus the"
            " checkout sidebar that displays them. Purge after"
            " enabling/disabling a PayWay or changing its cost."
        ),
        django_patterns=("*PayWayViewSet_*",),
        nuxt_patterns=nuxt_handlers("PayWayViewSet"),
        # Operator-driven and rare, so purging on write is cheap —
        # and necessary: the checkout renders the admin-authored
        # description and instructions, which used to stay stale for
        # hours after an edit. ``PayWayTranslation`` is listed
        # separately because parler keeps each language in its own
        # row, and editing the Greek copy touches only that row.
        invalidated_by=(
            "pay_way.PayWay",
            "pay_way.PayWayTranslation",
        ),
        # ``/api/v1/shipping/options`` embeds each option's eligible
        # pay-ways (id + name), which the checkout renders as badges
        # on the delivery step. A PayWay edit makes that payload
        # stale too, and it is cached under the ``shipping`` surface
        # rather than this one.
        related=("shipping",),
        icon="payments",
        group="commerce",
    ),
)
