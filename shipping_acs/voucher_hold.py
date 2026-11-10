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
       ▲                                                  │ nothing is sent
       │  the wait is over and ACS answers a read, ───────┤
       │  or staff say ACS answers again                  ▼
       └────── ACS returns a voucher ──── ONE REQUEST FIRST ──► HELD, the wait doubled:
                                           │ one at a time       an unknown outcome, or an
                                           │                     HTTP 4xx that is not ACS's
                                           ├ ACS refuses: the next held order goes next
                                           └ never reached ACS: the same order goes again

**What each outcome of a voucher request does**, by what it proves. This
table is the rule; ``AcsService.create_voucher_for_order`` only reports
which row happened.

==========================================  =====================  ==========================
Outcome                                     The order              The hold
==========================================  =====================  ==========================
ACS returns a voucher                       saved                  ends, if one request was
(:func:`record_voucher`)                                           allowed first. Creating a
                                                                   voucher works.
ACS refuses in its own envelope             rejected, staff's to   stays "one request first";
(``ACSExecution_HasError``, a value-row     fix (#626); no longer  the turn is free at once.
``Error_Message``; :func:`record_refusal`)  held                   ACS executed a create, but
                                                                   nothing shows one succeeds.
An HTTP 4xx that is not ACS's envelope      rejected (#626); no    closes again, the wait
(the manual has 200, 403 and 406 only, so   longer held            doubled, if one request was
a 400/401/404/429 is a proxy's or a                                allowed first. It says
gateway's; :func:`record_no_answer`)                               nothing about ACS. Begins
                                                                   no hold.
Unknown (a lost answer;                     unknown outcome        begins, or closes again
:func:`record_unknown_outcome`)             (#626); no longer      with the wait doubled.
                                            held
Never reached ACS (a connection never       stays held if it was   unchanged; the turn ends
made, the request cap, 403/406, a hold                             with its lease.
that could not be read)
==========================================  =====================  ==========================

A refused or rejected order leaves the held requests, so it is never the
one request sent first twice: the next oldest held order is.

The state is a row in the store's schema, not a cache key: a cache flush
or an unreachable Redis must not reopen it (the request slots of
``shipping_acs.concurrency`` are skipped then), staff read it in the
admin, and each span stays as a record. A row that is lost only costs
what one more lost answer costs; a row that stays is ended by the first
voucher ACS returns, which the sweep keeps asking for.

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


def record_voucher() -> None:
    """ACS returned a voucher: a hold that had allowed one request first
    is over.

    A voucher while the hold is closed changes nothing: that request was
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
        "ACS voucher hold since %s ended: ACS returned a voucher",
        hold.started_at.isoformat(),
    )


def record_refusal() -> None:
    """ACS refused a voucher request in its own envelope: the turn of
    the one request is over, so the next held order can go at once.

    The hold itself stays as it is. Never raises: the caller is
    reporting that order's refusal.
    """
    try:
        with transaction.atomic():
            hold = _locked()
            if hold is None or hold.trial_until is None:
                return
            hold.trial_until = None
            hold.save(update_fields=["trial_until"])
    except Exception:
        logger.exception("Could not free the turn of the ACS voucher hold")


def record_no_answer(error: str) -> None:
    """A voucher request got an HTTP 4xx that is not ACS's envelope: a
    hold that had allowed one request first closes again.

    Without a hold it begins none, and a hold that is already closed is
    left as it is: the 4xx is a known outcome for its order and says
    nothing new about ACS. Never raises.
    """
    try:
        with transaction.atomic():
            hold = _locked()
            if hold is not None and hold.trial_allowed_at is not None:
                _close_again(hold, error)
    except Exception:
        logger.exception("Could not close the ACS voucher hold again")


def record_unknown_outcome(error: str) -> None:
    """A voucher request this store sent ended with an unknown outcome:
    begin a hold, or close the one in force again.

    Never raises: the caller is recording that order's own unknown
    outcome, which must not be lost to a failure here.
    """
    try:
        with transaction.atomic():
            hold = _locked() or _begin(error)
            if hold is not None:
                _close_again(hold, error)
    except Exception:
        logger.exception("Could not record the ACS voucher hold")


def _begin(error: str) -> AcsVoucherHold | None:
    """Begin a hold; the one another worker began in the same instant is
    returned instead, to be closed again like any hold in force."""
    now = timezone.now()
    try:
        with transaction.atomic():
            AcsVoucherHold.objects.create(
                started_at=now, last_failure_at=now, reason=error
            )
    except IntegrityError:
        return _locked()
    logger.error(
        "ACS voucher requests are held: a voucher request has an unknown "
        "outcome (%s). Nothing more is sent until ACS answers again.",
        error,
    )
    return None


def _close_again(hold: AcsVoucherHold, error: str) -> None:
    """Record a failure on a hold in force, on a row the caller holds
    locked: nothing is sent, and a failed first request doubles the wait."""
    if hold.trial_allowed_at is not None:
        hold.failed_trials += 1
        logger.error(
            "ACS voucher hold since %s closed again: the request sent "
            "first did not get a voucher (%s)",
            hold.started_at.isoformat(),
            error,
        )
    hold.trial_allowed_at = None
    hold.trial_until = None
    hold.last_failure_at = timezone.now()
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
