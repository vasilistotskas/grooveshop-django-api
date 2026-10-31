"""The ACS operational alerts: their types, builders and samples.

Three mails to the store's own staff about a parcel or COD money that needs
a person: shipments that stopped moving, delivered COD parcels ACS never
paid out, and payout rows that match no shipment. Each is off while the
store has no active ACS carrier. ``emails.EmailsConfig`` imports this module
at boot; ``shipping_acs.tasks`` and ``shipping_acs.services`` choose WHEN to
send and keep the claims and counters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.utils.translation import gettext_noop

from emails.declare import staff_email
from emails.gates import CarrierGate
from emails.types import RenderEnv
from shipping_acs.enum.shipment_state import AcsShipmentState
from tenant.credentials import tenant_site_name

if TYPE_CHECKING:
    from shipping_acs.models import AcsShipment

TEMPLATE_DIR = "emails/shipping_acs"

_acs = CarrierGate(carrier="acs", label=_("ACS carrier"))


@dataclass(frozen=True, slots=True)
class StaleShipments:
    """The shipments a digest reports and the clock it measures them by."""

    shipments: Sequence[AcsShipment]
    threshold_days: int
    as_of: datetime


@dataclass(frozen=True, slots=True)
class UnremittedCod:
    """Delivered COD parcels ACS has not reported a payout for. Each row
    carries ``order_id``, ``voucher_no``, ``cod_amount``, ``delivered_on``
    and ``days_since_delivery`` (``shipping_acs.tasks._unremitted_cod_rows``)."""

    rows: Sequence[Mapping[str, Any]]
    older_than_days: int


@dataclass(frozen=True, slots=True)
class UnattributablePayouts:
    """COD payout rows no order could be tied to: *unmatched* ones name a
    voucher the store does not know, *skipped* ones name none at all."""

    unmatched: int
    skipped: int
    cod_payment_date: date | None


def _stale_shipments(digest: StaleShipments, env: RenderEnv) -> dict[str, Any]:
    rows = []
    for shipment in digest.shipments:
        moved_at = shipment.last_event_at or shipment.created_at
        rows.append(
            {
                "voucher_no": shipment.voucher_no or "—",
                "order_id": shipment.order_id,
                "shipment_state": shipment.get_shipment_state_display(),
                "last_movement_at": moved_at,
                "days_stale": (digest.as_of - moved_at).days,
            }
        )
    return {
        "site": tenant_site_name(),
        "n": len(rows),
        "shipments": rows,
        "threshold_days": digest.threshold_days,
    }


def _unremitted_cod(report: UnremittedCod, env: RenderEnv) -> dict[str, Any]:
    return {
        "site": tenant_site_name(),
        "n": len(report.rows),
        "rows": report.rows,
        "older_than_days": report.older_than_days,
    }


def _unattributable_payouts(
    report: UnattributablePayouts, env: RenderEnv
) -> dict[str, Any]:
    return {
        "site": tenant_site_name(),
        "total": report.unmatched + report.skipped,
        "unmatched": report.unmatched,
        "skipped": report.skipped,
        "cod_payment_date": report.cod_payment_date,
    }


def _sample_stale_shipments(env: RenderEnv) -> StaleShipments:
    from shipping_acs.models import AcsShipment

    now = timezone.now()
    return StaleShipments(
        shipments=[
            AcsShipment(
                voucher_no=voucher,
                order_id=order_id,
                shipment_state=state,
                last_event_at=now - timedelta(days=days),
            )
            for voucher, order_id, state, days in (
                ("9700000001", 1201, AcsShipmentState.NEW, 5),
                ("9700000002", 1188, AcsShipmentState.PENDING_CREATION, 9),
            )
        ],
        threshold_days=3,
        as_of=now,
    )


def _sample_unremitted_cod(env: RenderEnv) -> UnremittedCod:
    return UnremittedCod(
        rows=[
            {
                "order_id": 1201,
                "voucher_no": "9772892603",
                "cod_amount": "24.48 €",
                "delivered_on": "2026-09-30",
                "days_since_delivery": 30,
            },
            {
                "order_id": 1188,
                "voucher_no": "9772892710",
                "cod_amount": "61.00 €",
                "delivered_on": "2026-10-02",
                "days_since_delivery": 28,
            },
        ],
        older_than_days=7,
    )


staff_email(
    "ops.stale_shipments",
    area="ops",
    title=_("Stale shipments digest"),
    description=_(
        "Lists the ACS parcels that have shown no tracking movement for "
        "several days."
    ),
    templates=f"{TEMPLATE_DIR}/stale_shipments_alert",
    subject=gettext_noop("[{site}] Stale ACS shipment alert — {n} shipment(s)"),
    build_context=_stale_shipments,
    sample=_sample_stale_shipments,
    gates=(_acs,),
)
staff_email(
    "ops.unremitted_cod",
    area="ops",
    title=_("COD parcels never remitted"),
    description=_(
        "Lists the delivered COD parcels ACS has not paid out, with their "
        "voucher numbers to chase."
    ),
    templates=f"{TEMPLATE_DIR}/unremitted_cod_alert",
    subject=gettext_noop(
        "[{site}] ACS COD: {n} delivered parcel(s) never remitted"
    ),
    build_context=_unremitted_cod,
    sample=_sample_unremitted_cod,
    gates=(_acs,),
)
staff_email(
    "ops.unattributable_cod_payouts",
    area="ops",
    title=_("COD payouts that match no shipment"),
    description=_(
        "Tells the store's staff that ACS paid COD money that could not be "
        "tied to an order."
    ),
    templates=f"{TEMPLATE_DIR}/unattributable_cod_payouts_alert",
    subject=gettext_noop(
        "[{site}] ACS COD reconcile: {total} unattributable payout row(s)"
    ),
    build_context=_unattributable_payouts,
    sample=lambda env: UnattributablePayouts(
        unmatched=2, skipped=1, cod_payment_date=date(2026, 10, 29)
    ),
    gates=(_acs,),
)
