"""Querysets shared by the carrier shipment models."""

from __future__ import annotations

from django.db import models

#: ``metadata`` key the demo seed (``devtools/demo_shipments.py``) stamps
#: on the carrier rows it writes.
DEMO_SEED_KEY = "demo_seed"


class ShipmentQuerySet(models.QuerySet):
    def real(self) -> ShipmentQuerySet:
        """Shipments a carrier actually knows about.

        The demo store's fixture shipments carry invented vouchers. Every
        sweep that calls a carrier API, or alerts a merchant about what
        the carrier has (or has not) reported, starts from here, so a
        demo tenant that is ever given carrier credentials cannot poll,
        manifest or chase a voucher that does not exist.
        """
        return self.exclude(metadata__has_key=DEMO_SEED_KEY)
