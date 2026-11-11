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
                                           └ never reached ACS: the turn is free at once

**What each outcome of a voucher request does**, by what it proves. This
table is the rule; ``AcsService.create_voucher_for_order`` only reports
which row happened. The last column is for the request that has the turn
(see "Whose outcome" below).

==========================================  =====================  ==========================
Outcome                                     The order              The hold, when the request
                                                                   has the turn
==========================================  =====================  ==========================
ACS returns a voucher                       saved                  ends. Creating a voucher
(:func:`record_voucher`)                                           works.
ACS refuses in its own envelope             rejected, staff's to   stays "one request first";
(``ACSExecution_HasError``, a value-row     fix (#626); no longer  the turn is free at once.
``Error_Message``; :func:`record_refusal`)  held                   ACS executed a create, but
                                                                   nothing shows one succeeds.
An HTTP 4xx that is not ACS's envelope      a held order stays     closes again, the wait
(the manual has 200, 403 and 406 only, so   held while a hold is   doubled. It says nothing
a 400/401/404/429 is a proxy's or a         in force. With none,   about ACS, and nothing
gateway's; :func:`record_no_answer`)        or if it was not       about the order. Begins no
                                            held: rejected (#626)  hold.
                                            and staff send it
                                            again
Unknown (a lost answer;                     unknown outcome        closes again, the wait
:func:`record_unknown_outcome`)             (#626); no longer      doubled.
                                            held
Never reached ACS (a connection never       stays held if it was   stays "one request first";
made, 403/406; :func:`record_not_sent`)                            the turn is free at once.
                                                                   ACS processed nothing, and
                                                                   that is known now.
Stopped before its turn (the request cap,   stays held if it was   unchanged: it never had
a hold that could not be read)                                     the turn.
==========================================  =====================  ==========================

**Whose outcome.** "One request first" is a turn, and the turn belongs to
the request that took it: ``AcsVoucherHold.trial_request`` names it (the
shipment and that request's mint claim, the identity a request has
everywhere else). Only that request settles the turn or counts as the
first request's result. Another request of the store can still be out
while the turn is taken: it left before the hold began, or its own turn
ended with its lease and the next request took it. Its outcome is its
order's (the middle column) and leaves the hold as it is:

* its voucher does not end the hold. It left before the lost answer the
  hold began with, or before the request now sent to find out, so it
  shows nothing newer; a voucher changes nothing while the hold is
  closed for the same reason. The request that has the turn decides.
* its refusal, its HTTP 4xx and its "never reached ACS" change nothing:
  the turn is not theirs to free, and the wait is not theirs to double.
* its unknown outcome begins a hold when there is none. On a hold in
  force it is recorded as the last failure (a closed hold waits from it
  again) and is not a failed first request: the wait is not doubled and
  the turn stays where it is.

**The turn is a lease**, for a worker that stops with it:
``client.worst_case_request_seconds`` (78 s at the default
``ACS_HTTP_TIMEOUT``), shorter than the sweep's 5-minute tick, so a turn
nobody gave back is over before the next tick and costs at most the tick
it was taken in. Every outcome that is known gives the turn back at once.
A request whose lease ran out still has the turn until another request
takes it, so its late answer still decides.

**With no hold in force a held order's request is an ordinary request
again** (the hold ended and the sweep is sending what waited). Whatever
answers it is handled as for any order, so nothing is repeated tick after
tick with nobody told: ACS's refusal and an HTTP 4xx that is not ACS's
both take the order out of the sweep and alert staff once (to fix the
address, or to send the request again). Only a hold in force keeps an
order held after such a 4xx, and the hold is then what staff see.

A refused order leaves the held requests, and the sweep sends the held
order it tried longest ago, so no order is the one request sent first on
every tick.

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


def _has_the_turn(hold: AcsVoucherHold | None, request_id: str) -> bool:
    """Whether ``request_id`` is the one request sent first: the last
    request that took the turn of a hold still in that state."""
    return (
        hold is not None
        and hold.trial_allowed_at is not None
        and bool(request_id)
        and hold.trial_request == request_id
    )


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


def admit(*, lease_seconds: float, request_id: str) -> None:
    """Let one voucher request through, or raise that it is held.

    Called by ``AcsClient`` inside the request slot, right before the
    POST: a request that waited for a slot behind the one that failed is
    decided here, after that failure was recorded. Without a hold it
    returns at once. With "one request first" allowed, ``request_id``
    takes the turn for ``lease_seconds`` (the longest a request can
    last), so a worker that dies with it does not block the next one for
    ever. A turn whose lease ran out is taken over the same way.
    """
    with transaction.atomic():
        hold = _locked()
        if hold is None:
            return
        now = timezone.now()
        if hold.holds(now):
            raise held_error(hold)
        hold.trial_until = now + timedelta(seconds=lease_seconds)
        hold.trial_request = request_id
        hold.save(update_fields=["trial_until", "trial_request"])
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
        _free_the_turn(hold)
        hold.save(
            update_fields=["trial_allowed_at", "trial_until", "trial_request"]
        )
    return True


def record_voucher(request_id: str) -> None:
    """ACS returned a voucher to ``request_id``: when that request has
    the turn, the hold is over.

    Any other request's voucher changes nothing (the module docstring,
    "Whose outcome"). Never raises: the caller holds a voucher number it
    has yet to save.
    """
    try:
        with transaction.atomic():
            hold = _locked()
            if hold is None or not _has_the_turn(hold, request_id):
                return
            hold.ended_at = timezone.now()
            _free_the_turn(hold)
            hold.save(
                update_fields=["ended_at", "trial_until", "trial_request"]
            )
    except Exception:
        logger.exception("Could not end the ACS voucher hold")
        return
    logger.warning(
        "ACS voucher hold since %s ended: ACS returned a voucher",
        hold.started_at.isoformat(),
    )


def record_refusal(request_id: str) -> None:
    """ACS refused ``request_id`` in its own envelope: when that request
    has the turn, the turn is over and the next held order can go at
    once.

    The hold itself stays as it is. Never raises: the caller is
    reporting that order's refusal.
    """
    _give_the_turn_back(request_id)


def record_not_sent(request_id: str) -> None:
    """``request_id`` never reached ACS (a connection never made, a
    403/406): when that request has the turn, the turn is over at once.

    ACS processed nothing and that is known now, so nothing is gained by
    keeping the next request out until the lease ends. The hold stays
    "one request first". Never raises.
    """
    _give_the_turn_back(request_id)


def record_no_answer(error: str, request_id: str) -> bool:
    """``request_id`` got an HTTP 4xx that is not ACS's envelope: when
    that request has the turn, the hold closes again.

    Without a hold it begins none, and from any other request it changes
    nothing: the 4xx is a known outcome and says nothing new about ACS.

    Returns whether a hold is in force, which is whether an order that
    was held stays held: with none, its request was an ordinary one and
    the 4xx is staff's to act on. Never raises; a hold that could not be
    read counts as in force, so the order is kept for the next tick
    rather than dropped on a failure here.
    """
    try:
        with transaction.atomic():
            hold = _locked()
            if hold is None:
                return False
            if _has_the_turn(hold, request_id):
                _record_failure(hold, error, of_the_first_request=True)
    except Exception:
        logger.exception("Could not close the ACS voucher hold again")
    return True


def record_unknown_outcome(error: str, request_id: str) -> None:
    """A voucher request this store sent ended with an unknown outcome:
    begin a hold, or record the failure on the one in force.

    From the request that has the turn it closes the hold again and
    doubles the wait. From any other request it is the last failure and
    no more. Never raises: the caller is recording that order's own
    unknown outcome, which must not be lost to a failure here.
    """
    try:
        with transaction.atomic():
            hold = _locked() or _begin(error)
            if hold is not None:
                _record_failure(
                    hold,
                    error,
                    of_the_first_request=_has_the_turn(hold, request_id),
                )
    except Exception:
        logger.exception("Could not record the ACS voucher hold")


def _begin(error: str) -> AcsVoucherHold | None:
    """Begin a hold; the one another worker began in the same instant is
    returned instead, to record the failure on like any hold in force."""
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


def _free_the_turn(hold: AcsVoucherHold) -> None:
    """No request has the turn, on a row the caller saves."""
    hold.trial_until = None
    hold.trial_request = ""


def _give_the_turn_back(request_id: str) -> None:
    try:
        with transaction.atomic():
            hold = _locked()
            if hold is None or not _has_the_turn(hold, request_id):
                return
            _free_the_turn(hold)
            hold.save(update_fields=["trial_until", "trial_request"])
    except Exception:
        logger.exception("Could not free the turn of the ACS voucher hold")


def _record_failure(
    hold: AcsVoucherHold, error: str, *, of_the_first_request: bool
) -> None:
    """Record a failure on a hold in force, on a row the caller holds
    locked. The first request's failure closes the hold again and
    doubles the wait; any other leaves the turn where it is."""
    if of_the_first_request:
        hold.failed_trials += 1
        hold.trial_allowed_at = None
        _free_the_turn(hold)
        logger.error(
            "ACS voucher hold since %s closed again: the request sent "
            "first did not get a voucher (%s)",
            hold.started_at.isoformat(),
            error,
        )
    hold.last_failure_at = timezone.now()
    hold.reason = error
    hold.save(
        update_fields=[
            "failed_trials",
            "trial_allowed_at",
            "trial_until",
            "trial_request",
            "last_failure_at",
            "reason",
        ]
    )
