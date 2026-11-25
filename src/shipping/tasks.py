"""Celery tasks of the shipping app."""

from __future__ import annotations

from core import celery_app
from core.cache.invalidation import mark_model_stale
from core.monitored_task import MonitoredTask


@celery_app.task(base=MonitoredTask)
def purge_carrier_gate_surfaces() -> None:
    """Purge what renders where the store's carriers deliver.

    Queued by ``tenant.signals`` when a carrier credential on ``Tenant``
    really changes (``tenant.credentials.CARRIER_CREDENTIAL_FIELDS``);
    runs in that store's schema. The credentials are part of a carrier's
    checkout gate (``shipping.zones.carrier_offers``), like the
    ``ShippingProvider`` row's switch, so the surfaces that declare that
    row are the ones made stale.
    """
    from shipping.models import ShippingProvider

    mark_model_stale(ShippingProvider)
