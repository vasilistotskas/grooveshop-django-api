"""Voucher requests wait while ACS is failing: the store's voucher hold.

A voucher request whose outcome is unknown is never sent again; staff
check that order at the ACS portal (#626). One lost answer costs one
check. An ACS outage that answers 5xx or accepts connections and hangs
would cost one check for every order placed while it lasts. So the first
unknown outcome begins an :class:`~shipping_acs.models.AcsVoucherHold`,
and while it is in force no ``ACS_Create_Voucher`` is sent for the store:
the request ends as :class:`AcsVoucherRequestHeldError` (nothing reached
ACS, a known outcome), the shipment is marked as held, and
``shipping_acs.tasks.issue_held_acs_vouchers`` sends it later.

::

    no hold ──an unknown outcome of a create we sent──► HELD
                                                          │ nothing is sent
          the wait is over and ACS answers a read, ───────┤
          or staff say ACS answers again                  ▼
    no hold ◄──ACS answers a create── ONE REQUEST FIRST ──an unknown outcome──► HELD
                                       │ one at a time             (the wait doubles)
                                       └ never reached ACS: the next one may go

The state is a row in the store's schema, not a cache key: a cache flush
or an unreachable Redis must not reopen it (the request slots of
``shipping_acs.concurrency`` are skipped then), staff read it in the
admin, and each span stays as a record. A row that is lost only costs
what one more lost answer costs; a row that stays is ended by the first
answered request, which the sweep keeps asking for.

Every function is one short transaction on the hold's row
(``select_for_update``), so "one request first" holds across workers:
whoever locks the row first takes the request, the rest are held.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from shipping_acs.exceptions import AcsVoucherRequestHeldError
from shipping_acs.models.voucher_hold import IN_FORCE, AcsVoucherHold

logger = logging.getLogger(__name__)

#: The one alias the hold keeps from ACS. Reads, and the other writes,
#: are sent as before: only a voucher request has no way back.
HELD_ALIAS = "ACS_Create_Voucher"


def in_force() -> AcsVoucherHold | None:
    """The store's hold while it is in force, else None."""
    return AcsVoucherHold.objects.filter(IN_FORCE).first()


def _locked() -> AcsVoucherHold | None:
    return AcsVoucherHold.objects.select_for_update().filter(IN_FORCE).first()


def holding() -> AcsVoucherHold | None:
    """The hold, when a voucher request arriving now must wait."""
    hold = in_force()
    if hold is not None and hold.holds(timezone.now()):
        return hold
    return None


def held_error(hold: AcsVoucherHold) -> AcsVoucherRequestHeldError:
    return AcsVoucherRequestHeldError(
        alias=HELD_ALIAS,
        error_message=(
            "Not sent: ACS voucher requests are held since "
            f"{hold.started_at.isoformat()} because ACS is failing."
        ),
    )


def admit(*, lease_seconds: float) -> None:
    """Let one voucher request through, or raise that it is held.

    Called by ``AcsClient`` inside the request slot, right before the
    POST: a request that waited for a slot behind the one that failed is
    decided here, after that failure was recorded. Without a hold it
    returns at once. With "one request first" allowed it takes that one
    request for ``lease_seconds`` (the longest a request can last), so a
    worker that dies with it does not block the next one for ever.
    """
    with transaction.atomic():
        hold = _locked()
        if hold is None:
            return
        now = timezone.now()
        if hold.holds(now):
            raise held_error(hold)
        hold.trial_until = now + timedelta(seconds=lease_seconds)
        hold.save(update_fields=["trial_until"])
    logger.info(
        "ACS voucher hold since %s: one request sent first",
        hold.started_at.isoformat(),
    )


def record_unknown_outcome(error: str) -> None:
    """A voucher request this store sent ended with an unknown outcome:
    begin a hold, or close the one in force again.

    Never raises: the caller is recording that order's own unknown
    outcome, which must not be lost to a failure here.
    """
    try:
        with transaction.atomic():
            _begin_or_close_again(error)
    except Exception:
        logger.exception("Could not record the ACS voucher hold")


def _begin_or_close_again(error: str) -> None:
    now = timezone.now()
    hold = _locked()
    if hold is None:
        try:
            with transaction.atomic():
                AcsVoucherHold.objects.create(
                    started_at=now, last_failure_at=now, reason=error
                )
        except IntegrityError:
            # Another worker's lost answer began it in the same instant.
            hold = _locked()
        else:
            logger.error(
                "ACS voucher requests are held: a voucher request has an "
                "unknown outcome (%s). Nothing more is sent until ACS "
                "answers again.",
                error,
            )
            return
    if hold is None:
        return
    if hold.trial_allowed_at is not None:
        # The one request sent first lost its answer too.
        hold.failed_trials += 1
        logger.error(
            "ACS voucher hold since %s closed again: the request sent "
            "first has an unknown outcome (%s)",
            hold.started_at.isoformat(),
            error,
        )
    hold.trial_allowed_at = None
    hold.trial_until = None
    hold.last_failure_at = now
    hold.reason = error
    hold.save(
        update_fields=[
            "failed_trials",
            "trial_allowed_at",
            "trial_until",
            "last_failure_at",
            "reason",
        ]
    )


def record_answer() -> None:
    """ACS answered a voucher request (a voucher, or a refusal): a hold
    that had allowed one request first is over.

    An answer while the hold is closed changes nothing: that request was
    in flight before the hold began, so it says nothing newer than the
    lost answer does. Never raises: the caller holds a voucher number it
    has yet to save.
    """
    try:
        with transaction.atomic():
            hold = _locked()
            if hold is None or hold.trial_allowed_at is None:
                return
            hold.ended_at = timezone.now()
            hold.trial_until = None
            hold.save(update_fields=["ended_at", "trial_until"])
    except Exception:
        logger.exception("Could not end the ACS voucher hold")
        return
    logger.warning(
        "ACS voucher hold since %s ended: ACS answered a voucher request",
        hold.started_at.isoformat(),
    )


def allow_trial() -> bool:
    """Let one request go first: ACS answered a read, or staff said it
    answers again. Returns whether a closed hold changed."""
    with transaction.atomic():
        hold = _locked()
        if hold is None or hold.trial_allowed_at is not None:
            return False
        hold.trial_allowed_at = timezone.now()
        hold.trial_until = None
        hold.save(update_fields=["trial_allowed_at", "trial_until"])
    return True
