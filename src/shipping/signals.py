"""Receivers of the shipping app, connected from ``ShippingConfig.ready``."""

from __future__ import annotations

from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from core.cache.invalidation import mark_model_stale
from shipping.models import ShippingZone, ShippingZoneRegion
from shipping.models.zone import setting_regions

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
    if not setting_regions(instance.pk):
        instance.sync_rate_countries()
    mark_model_stale(ShippingZoneRegion)


@receiver(
    [post_save, post_delete],
    sender=ShippingZoneRegion,
    dispatch_uid="shipping.zone_region_row_changed",
)
def zone_region_row_changed(sender, instance, **kwargs) -> None:
    """A ``ShippingZoneRegion`` row written or deleted as a row, not
    through ``zone.regions`` (a ``create``, a queryset ``delete``): the
    zone may have become a region zone or the base zone, so its rates'
    country is rewritten, as for a region-set change. A zone deleted with
    its rows takes its rates with it."""
    if setting_regions(instance.zone_id):
        return
    zone = ShippingZone.objects.filter(pk=instance.zone_id).first()
    if zone is not None:
        zone.sync_rate_countries()
    mark_model_stale(ShippingZoneRegion)
