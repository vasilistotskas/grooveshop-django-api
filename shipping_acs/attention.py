"""ACS work that is waiting for a person: the rules behind the alert emails.

Each rule is one queryset, read by two surfaces that must agree: the
operational email (``shipping_acs.tasks``) and the dashboard's "Needs
attention" counter (``admin.dashboard.store.queries``). A store that
switches an email off (``ops.*`` email types) therefore loses nothing: the
same parcels or payouts stay counted until someone handles them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from django.conf import settings
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from order.enum.status import OrderStatus
from order.managers.order import awaiting_online_payment_q
from shipping_acs.enum.charge_type import AcsChargeType
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.models import (
    AcsCodPayout,
    AcsShipment,
    AcsUnrecognisedVoucher,
    AcsVoucherHold,
)
from shipping_acs.models.voucher_hold import IN_FORCE

if TYPE_CHECKING:
    from django.db.models import QuerySet

# A shipment still ``pending_creation`` after this long means the voucher
# mint failed for good or was lost: minting normally takes seconds.
STRANDED_MINT_AFTER = timedelta(hours=24)


def stale_threshold_days() -> int:
    """Days without a tracking event before a parcel is stale."""
    return int(getattr(settings, "ACS_STALE_SHIPMENT_DAYS", 3))


def cod_remittance_days() -> int:
    """Days after delivery before a missing COD payout is overdue."""
    return int(getattr(settings, "ACS_COD_REMITTANCE_ALERT_DAYS", 10))


def stale_shipment_q(
    now: datetime | None = None, threshold_days: int | None = None
) -> Q:
    """Shipments nobody is moving, as a ``Q`` on ``AcsShipment``.

    Two classes (real prod cases, 2026-07-11):

    * **Stale tracking**: a voucher exists, the state is not terminal, and
      no tracking event for ``threshold_days`` days (``created_at`` when no
      event was ever recorded).
    * **Stranded mint**: ``pending_creation`` for over 24 hours, unless the
      order is an online one still waiting for its payment: its voucher is
      dispatched by the payment webhook, and the mint refuses it until then
      (``ShipmentAwaitingPaymentError``). Reporting it would invite staff
      to issue a voucher for unpaid goods.
    """
    now = now or timezone.now()
    cutoff = now - timedelta(
        days=stale_threshold_days()
        if threshold_days is None
        else threshold_days
    )
    stale_tracking = (
        Q(voucher_no__isnull=False)
        & ~Q(
            shipment_state__in=[
                AcsShipmentState.PENDING_CREATION,
                AcsShipmentState.DELIVERED,
                AcsShipmentState.RETURNED,
                AcsShipmentState.CANCELED,
                AcsShipmentState.LOST,
            ]
        )
        & (
            Q(last_event_at__lt=cutoff)
            | (Q(last_event_at__isnull=True) & Q(created_at__lt=cutoff))
        )
    )
    stranded_mint = (
        Q(
            shipment_state=AcsShipmentState.PENDING_CREATION,
            created_at__lt=now - STRANDED_MINT_AFTER,
        )
        & ~awaiting_online_payment_q("order__")
        & ~voucher_request_unknown_q()
    )
    return stale_tracking | stranded_mint


def voucher_request_unknown_q() -> Q:
    """Shipments whose voucher request has an unknown outcome, as a ``Q``.

    ACS may hold a voucher for the order, so no new request is sent until
    staff adopt the voucher or request again. They are their own alert
    (``ops.acs_voucher_request_unknown``), so the stale digest's
    "stranded mint" class leaves them out: its remedy, "Issue ACS voucher
    now", is exactly what must not happen to them.
    """
    return Q(voucher_request_unknown_at__isnull=False, voucher_no__isnull=True)


def voucher_request_unknown(shipment: AcsShipment) -> bool:
    """:func:`voucher_request_unknown_q` for one loaded shipment."""
    return (
        shipment.voucher_request_unknown_at is not None
        and not shipment.voucher_no
    )


def voucher_requests_unknown() -> QuerySet[AcsShipment]:
    """Every shipment with a voucher request staff must still check.

    All of them, the ones whose order no longer needs a voucher included:
    ACS may hold a billed voucher for those too, so they stay counted
    until staff adopt, request again or close.
    """
    return AcsShipment.objects.real().filter(voucher_request_unknown_q())


#: Order statuses in which an order needs no voucher any more.
ORDER_STATUSES_NEEDING_NO_VOUCHER: frozenset[str] = frozenset(
    {OrderStatus.CANCELED, OrderStatus.REFUNDED}
)


def needs_no_voucher_q() -> Q:
    """Shipments that need no voucher any more: retired (CANCELED) or
    whose order was canceled or refunded."""
    return Q(shipment_state=AcsShipmentState.CANCELED) | Q(
        order__status__in=ORDER_STATUSES_NEEDING_NO_VOUCHER
    )


def needs_no_voucher(shipment: AcsShipment) -> bool:
    """:func:`needs_no_voucher_q` for one loaded shipment."""
    return (
        shipment.shipment_state == AcsShipmentState.CANCELED
        or shipment.order.status in ORDER_STATUSES_NEEDING_NO_VOUCHER
    )


def voucher_requests_waiting() -> QuerySet[AcsShipment]:
    """Shipments of unknown outcome whose order still needs its voucher.

    Only these can adopt a voucher or request one again, and only these
    hold back deleting a voucher that matches no order: it may be theirs.
    The others are closed by staff (``AcsService.close_voucher_request``).
    """
    return voucher_requests_unknown().exclude(needs_no_voucher_q())


def voucher_hold_in_force_q() -> Q:
    """The store's voucher hold, while it is in force, as a ``Q`` on
    ``AcsVoucherHold``."""
    return IN_FORCE


def voucher_holds_in_force() -> QuerySet[AcsVoucherHold]:
    """The hold that keeps the store's voucher requests from ACS: one row
    or none (``shipping_acs.voucher_hold``)."""
    return AcsVoucherHold.objects.filter(voucher_hold_in_force_q())


def voucher_request_held_q() -> Q:
    """Shipments whose voucher request was held, as a ``Q``: nothing was
    sent to ACS for them, and no voucher was saved since."""
    return Q(voucher_request_held_at__isnull=False, voucher_no__isnull=True)


def voucher_requests_held() -> QuerySet[AcsShipment]:
    """The held voucher requests still to send, once ACS answers again.

    What ``issue_held_acs_vouchers`` sends. A request of unknown outcome
    is staff's to resolve and is never sent from here, and an order that
    no longer needs a voucher (canceled while it waited) is left alone.
    """
    return (
        AcsShipment.objects.real()
        .filter(voucher_request_held_q())
        .exclude(voucher_request_unknown_q())
        .exclude(needs_no_voucher_q())
    )


def unresolved_unrecognised_voucher_q() -> Q:
    """Vouchers ACS named that match no shipment and nobody resolved."""
    return Q(resolved_at__isnull=True)


def unrecognised_vouchers() -> QuerySet[AcsUnrecognisedVoucher]:
    """Every voucher ACS holds for the store that matches no shipment and
    that staff have not adopted, deleted at ACS or marked as checked."""
    return AcsUnrecognisedVoucher.objects.filter(
        unresolved_unrecognised_voucher_q()
    )


def stale_shipments(now: datetime | None = None) -> QuerySet[AcsShipment]:
    """Every stale shipment, alerted already or not."""
    return AcsShipment.objects.real().filter(stale_shipment_q(now))


def unremitted_cod_shipments(
    older_than_days: int | None = None, now: datetime | None = None
) -> QuerySet[AcsShipment]:
    """COD parcels ACS delivered but never remitted the cash for.

    Nothing else looks for these: the tracking poll and the stale-shipment
    watch both exclude terminal shipment states, so once a parcel reaches
    ``delivered`` it drops out of every other watch, and a COD parcel that
    is delivered but never paid out is precisely a delivered parcel
    (production order 73 sat that way for 108 days).

    Keyed on the payout table rather than ``payment_status`` because the
    payout row IS the evidence: ``AcsCodPayout`` exists only once ACS
    reports having remitted, and writing it is what flips the order paid.

    The one other way out is an operator's explicit settlement,
    ``AcsService.record_cod_received_outside_acs``: such a parcel carries
    ``cod_received_outside_acs_at`` and is excluded; no ``AcsCodPayout`` is
    ever invented for it.
    """
    days = cod_remittance_days() if older_than_days is None else older_than_days
    cutoff = (now or timezone.now()) - timedelta(days=days)
    return AcsShipment.objects.real().filter(
        ~Exists(AcsCodPayout.objects.filter(voucher_no=OuterRef("voucher_no"))),
        voucher_no__isnull=False,
        shipment_state=AcsShipmentState.DELIVERED,
        charge_type=AcsChargeType.COD,
        cod_amount__gt=0,
        delivery_date__lt=cutoff,
        cod_received_outside_acs_at__isnull=True,
    )


def unattributed_payout_q() -> Q:
    """Payouts nobody has tied to a shipment or signed off, as a ``Q``.

    Shared by the counter and the payout list's filter, so the row the
    dashboard counts is exactly the list it opens.
    """
    return Q(shipment__isnull=True, reconciled_at__isnull=True)


def unattributed_payouts() -> QuerySet[AcsCodPayout]:
    """Every payout row that ties to none of the store's shipments and
    that staff have not reconciled.

    That includes rows ACS reported with no readable voucher (stored with
    an empty ``voucher_no``) and rows for a voucher the store does not
    know. There is no time window: a row stays counted until a person
    reconciles it (the payout list's "Mark as reconciled" action).
    """
    return AcsCodPayout.objects.filter(unattributed_payout_q())
