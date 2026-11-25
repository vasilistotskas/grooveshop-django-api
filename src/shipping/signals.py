"""Receivers of the shipping app, connected from ``ShippingConfig.ready``."""

from __future__ import annotations

from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from core.cache.invalidation import mark_model_stale
from shipping.models import ShippingZone, ShippingZoneRegion

_CHANGES = frozenset({"post_add", "post_remove", "post_clear"})


@receiver(
    m2m_changed,
    sender=ShippingZone.regions.through,
    dispatch_uid="shipping.zone_regions_changed",
)
def zone_regions_changed(sender, instance, action, **kwargs) -> None:
    """A zone's region set changed: purge what renders where the store
    delivers, and rewrite the country its rates carry.

    ``zone.regions.add()`` writes its ``ShippingZoneRegion`` rows with
    ``bulk_create``, which sends no ``post_save``, so the surfaces that
    declare ``ShippingZoneRegion`` (``invalidated_by``) are marked here.
    A zone that gained or lost its regions became a region zone or the
    base zone (``ShippingZone.rate_country_id``). The set changes only
    from the zone's side (``regions`` has no reverse accessor), so
    ``instance`` is the zone.
    """
    if action not in _CHANGES:
        return
    # ``ShippingZone.set_regions`` syncs once, for the set it wrote.
    if not getattr(instance, "_setting_regions", False):
        instance.sync_rate_countries()
    mark_model_stale(ShippingZoneRegion)
