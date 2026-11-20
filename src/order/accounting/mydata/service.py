"""Public service functions coordinating the myDATA submission flow.

These functions are the only things :mod:`order.tasks` and the admin
layer should call. They:

1. Resolve config (enabled + credentials) — no-op if disabled.
2. Build the XML payload from the invoice.
3. Optionally pre-validate against the pinned XSD.
4. POST the payload, classify the response.
5. Persist the AADE identifiers (MARK / UID / qrUrl) on the invoice
   row with ``select_for_update`` so retries / concurrent webhook
   deliveries don't race.

Error handling:
- Transport errors (5xx, 429, network) bubble as ``MyDataTransportError``
  — caller's Celery task retries them.
- Auth errors bubble as ``MyDataAuthError`` — not retryable.
- Row-level ``ValidationError`` / ``XMLSyntaxError`` from AADE become
  ``MyDataValidationError`` — terminal; invoice stays ``REJECTED``.
- Error 228 (duplicate uid) triggers the Tier A.5 recovery path:
  ``recover_mark_for_document`` queries ``RequestTransmittedDocs`` and
  writes the existing MARK back if exactly one matching doc is found.
  If recovery fails for any reason the invoice stays ``REJECTED`` and
  the caller falls back to the existing manual-reconciliation path.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, timedelta
from enum import Enum
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from xmlschema import XMLSchemaValidationError

from core.store_settings import store_setting
from order.accounting.movement import (
    ReceiptMovement,
    receipt_movement,
    send_in_flight,
    stamp_the_parcels_left_unregistered,
)
from order.accounting.mydata.builder import build_credit_xml, build_receipt_xml
from order.accounting.mydata.client import MyDataClient
from order.accounting.mydata.config import MyDataConfig, load_config
from order.accounting.mydata.exceptions import (
    MyDataAuthError,
    MyDataDuplicateError,
    MyDataNotListedError,
    MyDataSendInFlight,
    MyDataTransportError,
    MyDataValidationError,
)
from order.accounting.mydata.parser import (
    RequestedDocResult,
    ResponseRow,
    TransmittedDoc,
    parse_requested_doc,
    parse_response_doc,
)
from order.accounting.mydata.types import (
    ERROR_DUPLICATE_UID,
    ERROR_MARK_NOT_FOUND,
    ERROR_NO_ANSWER,
    ERROR_PICKUP_POINT_UNKNOWN,
)
from order.accounting.mydata.validator import validate_invoice_doc
from order.models.retail_credit import RetailCredit
from shipping.interfaces import PickupPointUnknown

logger = logging.getLogger(__name__)


def submit_document(invoice: Any) -> ResponseRow | None:
    """Submit ``invoice`` to AADE ``SendInvoices``.

    Returns the parsed :class:`ResponseRow` on success (MARK + qrUrl
    persisted on the invoice). Returns ``None`` when myDATA is not
    enabled / not configured — callers treat this as "integration off",
    NOT as failure.

    Raises one of the :class:`MyDataError` subclasses when the
    submission definitively failed.

    A MARK is AADE's registration and nothing undoes it: every write below
    re-reads the row under its lock and leaves a row that holds one as it
    is, returning it as registered (:func:`_registered_row`). Several
    senders may overlap (the task's retry chain, the sweep, staff, the
    SHIPPED trigger); they send the same uid, AADE registers one and
    answers the others with error 228, and none of them can demote the
    registered row to REJECTED or SUBMITTED.
    """
    if invoice.mydata_mark:
        # Already transmitted (it has a MARK): re-submitting would only be
        # answered with 228. Return the existing identifiers as a
        # synthetic Success so the caller treats it as already-done
        # (idempotent), mirroring the 228-recovery path (G0262).
        return _registered_row(invoice)

    config = load_config()
    if not config.is_ready():
        logger.debug(
            "myDATA integration not ready (enabled=%s, creds present=%s). "
            "Skipping submit for invoice %s.",
            config.enabled,
            bool(config.user_id and config.subscription_key),
            invoice.document_number,
        )
        return None

    issuer_vat = _resolve_issuer_vat()
    if not issuer_vat:
        raise MyDataValidationError(
            "INVOICE_SELLER_VAT_ID is empty — cannot submit to myDATA. "
            "Fill it in via Settings admin first.",
            code="MISSING_SELLER_VAT",
        )

    look_up = True
    while True:
        if look_up:
            verdict = _look_up_before_sending(
                invoice, config=config, issuer_vat=issuer_vat
            )
            if verdict is MarkLookup.FOUND:
                return _registered_row(invoice)
            look_up = False
        try:
            built = _build_document(
                invoice,
                config=config,
                issuer_vat=issuer_vat,
                nothing_registered=verdict is MarkLookup.ABSENT,
            )
        except ValueError as exc:
            # Unsupported VAT rate or malformed invoice_number — bad
            # master data, not transient. Record as a terminal validation
            # failure so the admin UI surfaces the reason rather than
            # retrying 5 times and then burying the error. A pickup point
            # the carrier's catalogue lacks has its own code: the sweep
            # builds it again once a sync may have added it.
            code = (
                ERROR_PICKUP_POINT_UNKNOWN
                if isinstance(exc, PickupPointUnknown)
                else "BUILD"
            )
            if not _persist_failure(
                invoice,
                code=code,
                message=f"Failed to build myDATA payload: {exc}",
                before_sending=True,
                verdict=verdict,
            ):
                return _registered_row(invoice)
            raise MyDataValidationError(str(exc), code=code) from exc

        try:
            validate_invoice_doc(built.xml_bytes)
        except XMLSchemaValidationError as exc:
            # XSD validation error is terminal: the payload does not
            # match the pinned schema. Fall into the same rejection
            # bucket as a server-side ValidationError so ops can see
            # the reason in the admin instead of a zombie SUBMITTED row.
            if not _persist_failure(
                invoice,
                code="XSD",
                message=f"Local XSD validation failed: {exc}",
                before_sending=True,
                verdict=verdict,
            ):
                return _registered_row(invoice)
            raise MyDataValidationError(str(exc), code="XSD") from exc

        # The movement block this send carries, stored with it: ``{}`` for
        # a receipt sent plain (its dispatch point cleared since a block
        # was stored), so the row never keeps a block AADE was not sent.
        # A credit has none.
        movement = (
            None
            if isinstance(invoice, RetailCredit)
            else (built.movement.as_json() if built.movement else {})
        )
        # Persist UID + request-scope identity BEFORE the HTTP call so a
        # transport failure leaves enough state to recover on retry.
        claim = _persist_submission_intent(
            invoice,
            uid=built.uid,
            invoice_type=built.invoice_type,
            series=built.series,
            aa=built.aa,
            movement=movement,
            verdict=verdict,
            config=config,
        )
        if claim is _SendClaim.REGISTERED:
            # Another sender registered it since this one read the row.
            return _registered_row(invoice)
        if claim is _SendClaim.CLAIMED:
            break
        if claim is _SendClaim.IN_FLIGHT:
            raise _in_flight_error(invoice)
        if claim is _SendClaim.ADVANCED:
            # Another sender began a send since this one read the row: its
            # lookup said nothing of that send, so the row is read again
            # and looked up again before anything is built.
            invoice.refresh_from_db()
            look_up = True
        # _SendClaim.STAMPED: the parcel left unregistered while this
        # sender looked its MARK up, so the document it built lacks the
        # loss-of-connection indication (and may carry a redated start).
        # The stamp is set once, so it is built again at most once.

    client = MyDataClient(config)
    try:
        response = parse_response_doc(client.send_invoices(built.xml_bytes))
    except MyDataTransportError as exc:
        # No answer, or none that can be read (an empty or cut-off body).
        _persist_send_error(invoice, code=ERROR_NO_ANSWER, message=exc.message)
        raise
    except MyDataAuthError as exc:
        # AADE refused the credentials, so the document was not taken in:
        # recorded, so the sweep leaves it to staff instead of resending it
        # as a send whose answer was lost.
        _persist_send_error(invoice, code=exc.code, message=exc.message)
        raise
    row = response.first()

    if row.is_success:
        _persist_success(invoice, row, movement=movement)
        return row

    # AADE explicitly distinguishes ``XMLSyntaxError`` from
    # ``ValidationError`` but both are terminal for our flow — we
    # collapse them into a single rejected state with the first
    # error code preserved.
    first_error = row.errors[0] if row.errors else None
    code = first_error.code if first_error else ""
    message = first_error.message if first_error else row.status_code

    if code == ERROR_DUPLICATE_UID:
        # Tier A.5 recovery: AADE already has this uid registered under
        # a MARK, from a send whose answer we never read or from another
        # sender. A row another sender registered meanwhile is returned
        # as it is; otherwise the error is persisted (REJECTED + uid, so
        # recover_mark_for_document can use the uid) and the MARK is
        # recovered via RequestTransmittedDocs.
        if not _persist_failure(invoice, code=code, message=message):
            return _registered_row(invoice)
        if recover_mark_for_document(
            invoice, config=config, issuer_vat=issuer_vat
        ):
            return _registered_row(invoice)
        raise MyDataDuplicateError(message, code=code)

    if row.status_code == "TechnicalError":
        # AADE gateway fault — includes the empty-200 body the parser
        # synthesises into a TechnicalError row (auth/routing hiccups).
        # This is a TRANSPORT fault, not a terminal document rejection:
        # do NOT ``_persist_failure`` (which would flip the invoice to
        # REJECTED and skip all retries). Leave ``mydata_status=SUBMITTED``
        # and raise the retryable transport error so the Celery task's
        # backoff and the manual-resubmit path keep working (G0260).
        _persist_send_error(
            invoice,
            code=ERROR_NO_ANSWER,
            message=message or "AADE TechnicalError",
        )
        raise MyDataTransportError(message or "AADE TechnicalError", code=code)

    if not _persist_failure(invoice, code=code, message=message):
        return _registered_row(invoice)
    raise MyDataValidationError(message, code=code)


def _registered_row(invoice: Any) -> ResponseRow:
    """A synthetic Success for a document that holds its MARK already (a
    send before this one registered it, or the 228 recovery found it), so
    the caller treats it as registered and chains what follows."""
    return ResponseRow(
        index=0,
        status_code="Success",
        invoice_uid=invoice.mydata_uid or "",
        invoice_mark=invoice.mydata_mark,
    )


def _persist_send_error(invoice: Any, *, code: str, message: str) -> None:
    """Record why the send just made has no registration, the status left
    SUBMITTED: ``ERROR_NO_ANSWER`` when AADE did not answer (or not
    readably), so it may be registered, the next send first looks its MARK
    up, and ``order.tasks.retry_receipts_with_mydata`` sends a receipt with
    movement again until it is; AADE's own code when it refused the
    credentials. A row registered meanwhile by another sender is left as
    it is.

    The answer belongs to the send *invoice* made (``mydata_sent_at``, as
    its claim recorded it): it is written as the row's latest outcome only
    while no other send began since, so a late answer never makes a newer
    send look finished. A send AADE did not answer is recorded as
    undecided either way (``mydata_unanswered_since``, from that send's
    start unless an earlier one is on record); a refused one leaves an
    earlier undecided send as it is.

    A receipt whose parcel is still with the store is sent again plainly.
    AADE's loss-of-connection indication (``transmissionFailure``,
    ``Invoice.transmission_failed_at``) belongs to a receipt with movement
    whose parcel left without a MARK because myDATA did not answer:
    stamped here when the parcel left before this answer while a send of it
    is undecided and none may still be in flight (the latest one ended:
    this one, one AADE answered, or one past its deadline,
    ``movement.send_in_flight``), whichever send owns the row's outcome;
    by ``movement.parcel_left_unregistered`` when it leaves after."""

    attempt = invoice.mydata_sent_at
    with transaction.atomic():
        locked = (
            type(invoice)
            ._default_manager.select_for_update()
            .get(pk=invoice.pk)
        )
        if locked.mydata_mark:
            return
        latest = locked.mydata_sent_at == attempt
        fields = []
        if code == ERROR_NO_ANSWER and attempt is not None:
            since = locked.mydata_unanswered_since
            if since is None or attempt < since:
                locked.mydata_unanswered_since = attempt
                fields.append("mydata_unanswered_since")
        if latest:
            locked.mydata_error_code = code[:10]
            locked.mydata_error_message = message or ""
            fields += [
                "mydata_error_code",
                "mydata_error_message",
                "updated_at",
            ]
        now = timezone.now()
        another_in_flight = not latest and send_in_flight(locked, at=now)
        if (
            locked.mydata_unanswered_since is not None
            and not another_in_flight
            and getattr(locked, "movement", None)
            and locked.transmission_failed_at is None
            and locked.order.shipped_at is not None
        ):
            locked.transmission_failed_at = now
            fields.append("transmission_failed_at")
            invoice.transmission_failed_at = now
        if fields:
            locked.save(update_fields=fields)
    if latest:
        invoice.mydata_error_code = code[:10]
        invoice.mydata_error_message = message or ""
    invoice.mydata_unanswered_since = locked.mydata_unanswered_since


def _in_flight_error(invoice: Any) -> MyDataSendInFlight:
    """The send *invoice*'s row records may still be running: nothing is
    sent over it, and nothing replaces it, before its deadline; the sweep
    takes it up once the deadline passed with no answer."""
    return MyDataSendInFlight(
        f"A send of {invoice.document_number} to myDATA may still be running "
        f"until {invoice.mydata_send_deadline:%Y-%m-%d %H:%M:%S}; nothing is "
        "sent over it before then."
    )


def _look_up_before_sending(
    invoice: Any, *, config: MyDataConfig, issuer_vat: str
) -> MarkLookup | None:
    """Look the MARK of *invoice* up before it is built and sent again, when
    it may be registered: its latest send has no answer yet (SUBMITTED, with
    a uid: unanswered, or its sender stopped mid-request), or an earlier one
    went unanswered and nothing decided it since
    (``mydata_unanswered_since``, whatever refused a later send). ``None``
    when nothing of it can be registered. Asked with the *config* and
    *issuer_vat* the resend goes with.

    Finding nothing decides only sends that could no longer register when
    AADE was asked: before the row's deadline (``mydata_send_deadline``,
    the latest any of its sends may still land) one may still register
    after the listing was read, so the verdict is ``UNKNOWN`` then. What the lookup concludes of the send the row
    records (``mydata_sent_at``) is written with the next write that
    replaces it (:func:`_settle_the_last_send`), and only if no other send
    began since."""
    from order.models.invoice import MyDataStatus

    if not invoice.mydata_uid or (
        invoice.mydata_status != MyDataStatus.SUBMITTED
        and invoice.mydata_unanswered_since is None
    ):
        return None
    asked_at = timezone.now()
    try:
        verdict = look_up_mark(invoice, config=config, issuer_vat=issuer_vat)
    except MyDataAuthError as exc:
        # AADE refused the credentials the lookup asked with: that says
        # nothing of the document, which stays as it is (and stamped when
        # its parcel leaves).
        _persist_lookup_refusal(invoice, message=exc.message)
        raise
    if (
        verdict is MarkLookup.ABSENT
        and invoice.mydata_send_deadline is not None
        and asked_at < invoice.mydata_send_deadline
    ):
        return MarkLookup.UNKNOWN
    return verdict


def _settle_the_last_send(locked: Any, verdict: MarkLookup | None) -> list[str]:
    """The send *locked* records last (``mydata_sent_at``) is about to be
    replaced: by a new send, or by a refusal of one built but never sent.
    The caller has checked it is the send this sender's lookup covered.
    Nothing registered under its uid (*verdict* ``ABSENT``) decides it and
    every earlier one; otherwise a send that may still be held (no answer,
    or none on record) is kept as undecided
    (``mydata_unanswered_since``), so no later error can hide it. Returns
    the fields written."""
    from order.models.invoice import MyDataStatus

    if verdict is MarkLookup.ABSENT:
        if locked.mydata_unanswered_since is None:
            return []
        locked.mydata_unanswered_since = None
        return ["mydata_unanswered_since"]
    if (
        locked.mydata_unanswered_since is None
        and locked.mydata_status == MyDataStatus.SUBMITTED
        and locked.mydata_error_code in ("", ERROR_NO_ANSWER)
    ):
        locked.mydata_unanswered_since = locked.mydata_sent_at
        return ["mydata_unanswered_since"]
    return []


def _persist_lookup_refusal(invoice: Any, *, message: str) -> None:
    """Record that AADE refused the credentials the lookup of *invoice*'s
    MARK asked with, keeping its last send's outcome (``mydata_error_code``)
    and whether a send of it is undecided: the document may still be
    registered. Staff read why (``mydata_error_message``), and the touch
    spaces the sweep's next try by ``MYDATA_RETRY_AFTER_MINUTES``, which
    registers it once the store's credentials work again. Written only
    while the row records the send the lookup was about: a send that began
    since, or a registration, is left as it is."""
    with transaction.atomic():
        locked = (
            type(invoice)
            ._default_manager.select_for_update()
            .get(pk=invoice.pk)
        )
        if (
            locked.mydata_mark
            or locked.mydata_sent_at != invoice.mydata_sent_at
        ):
            return
        locked.mydata_error_message = message
        locked.save(update_fields=["mydata_error_message", "updated_at"])
    invoice.mydata_error_message = message


def cancel_document(invoice: Any) -> ResponseRow | None:
    """Cancel a previously-submitted invoice via ``CancelInvoice``.

    Only valid when the invoice has an ``mydata_mark`` — we don't
    support cancelling a document that was never transmitted. Returns
    ``None`` when myDATA is off; raises on AADE failure.
    """
    if not invoice.mydata_mark:
        raise MyDataValidationError(
            "Cannot cancel — invoice has no MARK (never transmitted).",
            code="NO_MARK",
        )

    config = load_config()
    if not config.is_ready():
        return None

    client = MyDataClient(config)
    response_bytes = client.cancel_invoice(invoice.mydata_mark)
    response = parse_response_doc(response_bytes)
    row = response.first()

    if row.is_success and row.cancellation_mark:
        _persist_cancellation(invoice, cancellation_mark=row.cancellation_mark)
        return row

    first_error = row.errors[0] if row.errors else None
    code = first_error.code if first_error else ""
    message = first_error.message if first_error else row.status_code

    if row.status_code == "TechnicalError" or (
        code == ERROR_MARK_NOT_FOUND and _registered_moments_ago(invoice)
    ):
        # Transient AADE gateway fault — retryable, not a terminal
        # rejection. See ``submit_invoice`` for the full rationale (G0260).
        # So is a document the cancel cannot see yet: AADE answers 301
        # to a cancel sent right after the registration.
        raise MyDataTransportError(message or "AADE TechnicalError", code=code)

    # A FAILED cancellation must NOT flip the invoice from CONFIRMED to
    # REJECTED — it is still validly transmitted at AADE; only the cancel
    # attempt failed. Record the error without touching mydata_status (G0262).
    _persist_error_only(invoice, code=code, message=message)
    raise MyDataValidationError(message, code=code)


def _registered_moments_ago(document: Any) -> bool:
    """Whether AADE registered *document* (``mydata_confirmed_at``) within
    ``MYDATA_CANCEL_NOT_FOUND_RETRY_MINUTES``: the time a cancel may be
    answered 301 because AADE does not show it the document yet. A row
    with no registration time is past it."""
    registered = document.mydata_confirmed_at
    return registered is not None and timezone.now() - registered <= (
        timedelta(minutes=settings.MYDATA_CANCEL_NOT_FOUND_RETRY_MINUTES)
    )


def find_cancellation_mark(invoice: Any) -> int | None:
    """The MARK of the cancellation AADE holds for *invoice*, or ``None``
    when AADE holds none: ``RequestTransmittedDocs`` lists the document
    with its ``cancelledByMark`` once it is canceled, and a cancellation of
    it in ``cancelledInvoicesDoc`` (myDATA REST API v2.0.2, §4.2.7 and
    §6.2).

    The search starts at the document's own MARK and is bounded by its
    issue date (:func:`_issue_window`, as the 228 recovery's), never by a
    page count: on a busy VAT the documents issued after it that day are
    the only other rows, and every page of them is read.

    Asked before a canceled order's receipt goes to the credit path, so a
    cancel whose answer was lost never ends as a cancellation and a
    credit. Raises ``MyDataTransportError`` when AADE cannot be asked, and
    ``MyDataNotListedError`` (one of them) when it answers without listing
    the document at all (nothing can be concluded).
    """
    config = load_config()
    issuer_vat = _resolve_issuer_vat()
    if not (config.is_ready() and issuer_vat and invoice.mydata_mark):
        raise MyDataTransportError(
            "myDATA cannot be asked about the document now."
        )
    date_from, date_to = _issue_window(invoice.issue_date)
    listed = False
    for page in _requested_pages(
        MyDataClient(config),
        entity_vat_number=issuer_vat,
        after_mark=invoice.mydata_mark - 1,
        date_from=date_from,
        date_to=date_to,
    ):
        if invoice.mydata_mark in page.cancellations:
            return page.cancellations[invoice.mydata_mark]
        for doc in page.docs:
            if doc.invoice_mark != invoice.mydata_mark:
                continue
            if doc.cancelled_by_mark:
                return doc.cancelled_by_mark
            listed = True
    if not listed:
        raise MyDataNotListedError(
            f"myDATA does not list the document with MARK "
            f"{invoice.mydata_mark} issued on {invoice.issue_date}."
        )
    return None


def _issue_window(issue_date: date) -> tuple[date, date]:
    """The ``RequestTransmittedDocs`` date window of a document: a day
    either side of its issue date. AADE filters on the issue date, and the
    margin covers a timezone edge without reading weeks of history."""
    return issue_date - timedelta(days=1), issue_date + timedelta(days=1)


def _requested_pages(
    client: MyDataClient, **query: Any
) -> Iterator[RequestedDocResult]:
    """Every page ``RequestTransmittedDocs`` lists for *query*, following
    its continuation token to the end. The query (a MARK and a date window)
    is what bounds the read, never a page count, so a busy VAT is read
    whole. A token AADE hands back a second time would loop forever: that
    raises ``MyDataTransportError``, as any other answer that cannot be
    used."""
    seen: set[tuple[str | None, str | None]] = set()
    token: tuple[str | None, str | None] = (None, None)
    while True:
        page = parse_requested_doc(
            client.request_transmitted_docs(
                **query,
                next_partition_key=token[0],
                next_row_key=token[1],
            )
        )
        yield page
        token = (page.next_partition_key, page.next_row_key)
        if token == (None, None):
            return
        if token in seen:
            raise MyDataTransportError(
                "myDATA repeated a continuation token of "
                "RequestTransmittedDocs."
            )
        seen.add(token)


def record_cancellation(invoice: Any, *, cancellation_mark: int) -> None:
    """Record that AADE holds a cancellation of *invoice* (a cancel whose
    answer was lost, found by :func:`find_cancellation_mark`)."""
    _persist_cancellation(invoice, cancellation_mark=cancellation_mark)


class MarkLookup(Enum):
    """What AADE says of a document we may have sent: it holds it under
    our uid (``FOUND``, its MARK now recorded), it lists no document under
    that uid in the document's issue window (``ABSENT``: nothing of it is
    registered), or it could not be asked or gave an answer no safe
    conclusion can be drawn from (``UNKNOWN``)."""

    FOUND = "found"
    ABSENT = "absent"
    UNKNOWN = "unknown"


def recover_mark_for_document(
    invoice: Any,
    *,
    config: MyDataConfig | None = None,
    issuer_vat: str | None = None,
) -> bool:
    """Attempt to recover a MARK for an invoice that received error 228.

    Error 228 means AADE already has our ``uid`` registered under a MARK
    (from a prior successful transmission whose response we never
    received). Returns ``True`` when the MARK was recovered and the
    invoice flipped to ``CONFIRMED`` (:func:`look_up_mark`), ``False``
    otherwise; the caller (``send_invoice_to_mydata``) keeps the existing
    ``REJECTED`` state and manual-reconciliation path then. The send that
    got the 228 passes its own *config* and *issuer_vat*.
    """
    return (
        look_up_mark(invoice, config=config, issuer_vat=issuer_vat)
        is MarkLookup.FOUND
    )


def look_up_mark(
    invoice: Any,
    *,
    config: MyDataConfig | None = None,
    issuer_vat: str | None = None,
) -> MarkLookup:
    """Ask AADE whether it holds *invoice* under its uid:
    ``RequestTransmittedDocs`` scoped to the invoice's issue date ± 1 day
    and our entity VAT, and the single transmitted doc whose ``uid``
    matches ``invoice.mydata_uid``.

    SAFETY CONTRACT — the MARK is only written when ALL of:
      1. Exactly one transmitted doc matches the invoice's uid.
      2. That doc has a non-None ``invoiceMark``.
    A wrong MARK must be impossible.

    ``ABSENT`` only when the whole window was read and no document has
    the uid. ``UNKNOWN`` in every other case: config not ready, no uid,
    VAT or issue date to look up with, a transport error during the
    query, several matches (a uid collision, which should never happen),
    or a match without a MARK.

    A lookup made for a send asks with that send's *config* and
    *issuer_vat*, so it reads the AADE environment and VAT the send goes
    to; a lookup on its own reads them now.
    """
    uid_to_find = getattr(invoice, "mydata_uid", "") or ""
    if not uid_to_find:
        logger.warning(
            "recover_mark_for_invoice: invoice pk=%s has no mydata_uid "
            "— cannot query RequestTransmittedDocs",
            invoice.pk,
        )
        return MarkLookup.UNKNOWN

    if config is None:
        config = load_config()
    if not config.is_ready():
        logger.debug(
            "recover_mark_for_invoice: myDATA not ready, skipping recovery "
            "for invoice pk=%s",
            invoice.pk,
        )
        return MarkLookup.UNKNOWN

    if issuer_vat is None:
        issuer_vat = _resolve_issuer_vat()
    if not issuer_vat:
        logger.warning(
            "recover_mark_for_invoice: INVOICE_SELLER_VAT_ID is empty, "
            "cannot scope RequestTransmittedDocs query for invoice pk=%s",
            invoice.pk,
        )
        return MarkLookup.UNKNOWN

    # Scope the query as narrowly as possible: ±1 day around the invoice
    # issue date. AADE's date filter uses the document issue date so a
    # ±1-day window covers any timezone edge cases without pulling in
    # unrelated documents from weeks of history.
    issue_date = getattr(invoice, "issue_date", None)
    if issue_date is None:
        logger.warning(
            "recover_mark_for_invoice: invoice pk=%s has no issue_date, "
            "cannot scope the date window",
            invoice.pk,
        )
        return MarkLookup.UNKNOWN

    date_from, date_to = _issue_window(issue_date)

    # Every page of the window: it is bounded by the issue date, so a busy
    # VAT is read whole instead of being cut off after a page count.
    try:
        all_docs = [
            doc
            for page in _requested_pages(
                MyDataClient(config),
                entity_vat_number=issuer_vat,
                date_from=date_from,
                date_to=date_to,
            )
            for doc in page.docs
        ]
    except MyDataTransportError as exc:
        logger.warning(
            "recover_mark_for_invoice: transport error querying "
            "RequestTransmittedDocs for invoice pk=%s uid=%s: %s",
            invoice.pk,
            uid_to_find,
            exc,
        )
        # Do NOT re-raise — caller keeps REJECTED + logs.
        return MarkLookup.UNKNOWN

    # SAFETY: require exactly one match for our uid.
    matches = [d for d in all_docs if d.uid == uid_to_find]

    if len(matches) == 0 and any(not doc.uid for doc in all_docs):
        # A listed document without a uid (AADE's ``uid`` is optional in
        # ``AadeBookInvoiceType``) may be this one: nothing can be
        # concluded, and ABSENT would redate a document AADE may hold.
        logger.warning(
            "recover_mark_for_invoice: uid=%s not found, but the window "
            "%s–%s lists documents without a uid; invoice pk=%s may be "
            "one of them.",
            uid_to_find,
            date_from,
            date_to,
            invoice.pk,
        )
        return MarkLookup.UNKNOWN

    if len(matches) == 0:
        logger.warning(
            "recover_mark_for_invoice: uid=%s not found in "
            "RequestTransmittedDocs window %s–%s for invoice pk=%s "
            "(retrieved %d docs).",
            uid_to_find,
            date_from,
            date_to,
            invoice.pk,
            len(all_docs),
        )
        return MarkLookup.ABSENT

    if len(matches) > 1:
        # uid collision — should be mathematically impossible (SHA-1
        # over the seven-field tuple), but we must not silently pick
        # one arbitrarily.
        logger.error(
            "recover_mark_for_invoice: uid=%s matched %d docs in "
            "RequestTransmittedDocs (expected 1). Refusing to write "
            "any MARK for invoice pk=%s to avoid data corruption.",
            uid_to_find,
            len(matches),
            invoice.pk,
        )
        return MarkLookup.UNKNOWN

    matched = matches[0]
    if matched.invoice_mark is None:
        logger.warning(
            "recover_mark_for_invoice: uid=%s matched but invoiceMark "
            "is None for invoice pk=%s. Keeping REJECTED state.",
            uid_to_find,
            invoice.pk,
        )
        return MarkLookup.UNKNOWN

    # All safety checks passed — write the recovered MARK.
    logger.info(
        "recover_mark_for_invoice: recovered MARK=%s for invoice pk=%s "
        "uid=%s via RequestTransmittedDocs",
        matched.invoice_mark,
        invoice.pk,
        uid_to_find,
    )
    _persist_recovered_mark(invoice, matched)
    return MarkLookup.FOUND


def _build_document(
    invoice: Any, *, config: Any, issuer_vat: str, nothing_registered: bool
):
    """Build the payload for the document: a credit (11.4) for a
    :class:`RetailCredit`, otherwise the order's receipt (11.1), with its
    movement indication when the order moves through a carrier
    (``order.accounting.movement``; a ``ValueError`` there is a terminal
    BUILD failure like any other)."""
    if isinstance(invoice, RetailCredit):
        return build_credit_xml(
            invoice,
            issuer_vat=issuer_vat,
            issuer_country="GR",
            branch=config.issuer_branch,
            series_prefix=config.credit_series_prefix,
        )
    return build_receipt_xml(
        invoice,
        issuer_vat=issuer_vat,
        issuer_country="GR",
        branch=config.issuer_branch,
        series_prefix=config.invoice_series_prefix,
        movement=receipt_movement(
            invoice, nothing_registered=nothing_registered
        ),
    )


def _resolve_issuer_vat() -> str:
    """The seller VAT the store issues its documents under
    (``INVOICE_SELLER_VAT_ID``)."""
    return str(store_setting("INVOICE_SELLER_VAT_ID") or "")


class _SendClaim(Enum):
    """What :func:`_persist_submission_intent` found under the row lock."""

    #: The intent is stored: send the document built.
    CLAIMED = "claimed"
    #: Another sender registered it: send nothing.
    REGISTERED = "registered"
    #: Its loss-of-connection stamp (``transmission_failed_at``) landed
    #: after the document was built: build it again from the row.
    STAMPED = "stamped"
    #: Another sender began a send after this one read the row: read it
    #: again, and look it up again, before building anything.
    ADVANCED = "advanced"
    #: The send the row records may still be running
    #: (``movement.send_in_flight``): send nothing now.
    IN_FLIGHT = "in_flight"


def _persist_submission_intent(
    invoice: Any,
    *,
    uid: str,
    invoice_type: str,
    series: str,
    aa: int,
    config: MyDataConfig,
    movement: dict[str, Any] | None = None,
    verdict: MarkLookup | None = None,
) -> _SendClaim:
    """Lock + save the identity fields before the HTTP round-trip, and a
    receipt's movement indication with them (``Invoice.movement``, ``{}``
    for one sent plain), so the PDF registered after it prints what AADE
    received.

    Writes nothing when the row holds a MARK already (another sender
    registered it since this one read it: ``REGISTERED``, its registered
    state mirrored onto *invoice*), or when it was stamped as sent during
    a loss of connection after *invoice* was read (``STAMPED``, the stamp
    mirrored onto *invoice*): the document built from the stale copy
    misses ``transmissionFailure`` and is built again; so is one whose
    parcel left while a send of it has no answer and that nothing stamped
    yet, which this claim stamps first. The stamp lands
    only on a row whose send has no answer
    (``movement.unanswered_sends``), and this write starts a new send
    (``mydata_sent_at``), so once claimed it cannot land before the send.

    Each send is an attempt the row names by its start
    (``mydata_sent_at``), and this claim is a compare-and-set on it:
    another sender that began a send after *invoice* was read
    (``ADVANCED``) leaves this one to read the row and look it up again,
    since its lookup (*verdict*) said nothing of that send. A send the row
    records that may still be running (``IN_FLIGHT``, before its deadline
    with no answer on record) is never sent over: a lookup cannot decide
    it yet. Otherwise the verdict settles the send the row recorded before
    this one (:func:`_settle_the_last_send`), and this send's deadline
    (``mydata_send_deadline``) is its start plus the window *config* gives
    it now (``MyDataConfig.send_window``), never earlier than the one
    before: a send begun under a longer window may still register until
    then."""
    from order.models.invoice import MyDataStatus

    with transaction.atomic():
        locked = (
            type(invoice)
            ._default_manager.select_for_update()
            .get(pk=invoice.pk)
        )
        if locked.mydata_mark:
            _mirror_registration(invoice, locked)
            return _SendClaim.REGISTERED
        if locked.mydata_sent_at != invoice.mydata_sent_at:
            return _SendClaim.ADVANCED
        now = timezone.now()
        if send_in_flight(locked, at=now):
            invoice.mydata_send_deadline = locked.mydata_send_deadline
            return _SendClaim.IN_FLIGHT
        if not isinstance(locked, RetailCredit) and (
            stamp_the_parcels_left_unregistered(
                type(locked)._default_manager.filter(pk=locked.pk)
            )
        ):
            # Its parcel left while a send of it has no answer, and nothing
            # stamped it yet (a sender that stopped was still in flight when
            # the parcel left): stamped now, whoever sends it, and the
            # document is built again with the indication.
            locked.refresh_from_db(fields=["transmission_failed_at"])
        stamp = getattr(locked, "transmission_failed_at", None)
        if stamp != getattr(invoice, "transmission_failed_at", None):
            invoice.transmission_failed_at = stamp
            return _SendClaim.STAMPED
        settled = _settle_the_last_send(locked, verdict)
        locked.mydata_uid = uid
        locked.mydata_invoice_type = invoice_type
        locked.mydata_series = series
        locked.mydata_aa = aa
        locked.mydata_status = MyDataStatus.SUBMITTED
        if locked.mydata_submitted_at is None:
            locked.mydata_submitted_at = now
        locked.mydata_sent_at = now
        locked.mydata_send_deadline = max(
            deadline
            for deadline in (
                locked.mydata_send_deadline,
                now + config.send_window,
            )
            if deadline is not None
        )
        locked.mydata_error_code = ""
        locked.mydata_error_message = ""
        fields = [
            "mydata_uid",
            "mydata_invoice_type",
            "mydata_series",
            "mydata_aa",
            "mydata_status",
            "mydata_submitted_at",
            "mydata_sent_at",
            "mydata_send_deadline",
            "mydata_error_code",
            "mydata_error_message",
            "updated_at",
            *settled,
        ]
        if movement is not None:
            locked.movement = movement
            fields.append("movement")
        locked.save(update_fields=fields)
    # Mirror onto the in-memory instance so the caller sees the
    # updated fields without re-fetching.
    invoice.mydata_uid = uid
    invoice.mydata_invoice_type = invoice_type
    invoice.mydata_series = series
    invoice.mydata_aa = aa
    invoice.mydata_status = MyDataStatus.SUBMITTED
    invoice.mydata_sent_at = locked.mydata_sent_at
    invoice.mydata_send_deadline = locked.mydata_send_deadline
    invoice.mydata_unanswered_since = locked.mydata_unanswered_since
    invoice.mydata_error_code = ""
    invoice.mydata_error_message = ""
    if movement is not None:
        invoice.movement = movement
    return _SendClaim.CLAIMED


def _mirror_registration(invoice: Any, locked: Any) -> None:
    """Copy a registered row's identifiers onto the caller's stale copy."""
    invoice.mydata_status = locked.mydata_status
    invoice.mydata_mark = locked.mydata_mark
    invoice.mydata_uid = locked.mydata_uid
    invoice.mydata_qr_url = locked.mydata_qr_url


def _persist_success(
    invoice: Any, row: ResponseRow, *, movement: dict[str, Any] | None = None
) -> None:
    """Record AADE's registration of the document as this send made it.

    The facts of the registered document are written whatever the row
    holds: its MARK, AADE's codes, and the movement block this send
    carried, which is what AADE now holds even if an overlapping sender
    wrote another (a redated) block meanwhile. The status becomes CONFIRMED,
    unless the row was canceled in myDATA since (a MARK recovered by a
    lookup, then the cancel, while this answer was on its way): a
    cancellation is never undone."""
    from order.models.invoice import MyDataStatus

    with transaction.atomic():
        locked = (
            type(invoice)
            ._default_manager.select_for_update()
            .get(pk=invoice.pk)
        )
        fields = [
            "mydata_mark",
            "mydata_authentication_code",
            "mydata_qr_url",
            "mydata_unanswered_since",
            "updated_at",
        ]
        if not locked.mydata_mark:
            # A row registered already cleared its PDF then.
            _drop_the_unregistered_pdf(locked, fields)
        locked.mydata_mark = row.invoice_mark
        locked.mydata_authentication_code = row.authentication_code or ""
        locked.mydata_qr_url = row.qr_url or ""
        # AADE answered: no send of it is undecided any more.
        locked.mydata_unanswered_since = None
        if locked.mydata_status != MyDataStatus.CANCELED:
            locked.mydata_status = MyDataStatus.CONFIRMED
            locked.mydata_confirmed_at = timezone.now()
            locked.mydata_error_code = ""
            locked.mydata_error_message = ""
            fields += [
                "mydata_status",
                "mydata_confirmed_at",
                "mydata_error_code",
                "mydata_error_message",
            ]
        if movement is not None:
            locked.movement = movement
            fields.append("movement")
        locked.save(update_fields=fields)
    invoice.mydata_status = locked.mydata_status
    invoice.mydata_mark = row.invoice_mark
    invoice.mydata_qr_url = row.qr_url or ""
    invoice.document_file = locked.document_file.name or ""
    if movement is not None:
        invoice.movement = movement


def _drop_the_unregistered_pdf(locked: Any, fields: list[str]) -> None:
    """Clear the PDF *locked* holds from before its registration, in the
    save that records the MARK: it prints no MARK and no AADE QR, and
    nothing may hand it to the customer as the registered document
    (``Invoice.is_deliverable`` needs a stored PDF; the SHIPPED handler
    and a download read the row at any moment). The registration renders
    the registered one next (``order.tasks.send_invoice_to_mydata``), and
    a render that fails leaves a row with no PDF, which that task renders
    first when it runs again, with no second submission
    (``submit_document`` returns the MARK it holds). The file goes once
    the MARK is committed."""
    if not locked.document_file:
        return
    storage = locked.document_file.storage
    name = locked.document_file.name
    locked.document_file = ""
    fields.append("document_file")
    # robust: a file that cannot be removed is logged and left behind; the
    # registration it follows stands, and the send goes on to render the
    # registered PDF and email it.
    transaction.on_commit(lambda: storage.delete(name), robust=True)


def _persist_failure(
    invoice: Any,
    *,
    code: str,
    message: str,
    before_sending: bool = False,
    verdict: MarkLookup | None = None,
) -> bool:
    """Record a refusal: REJECTED, with its code and message. Returns
    ``False``, writing nothing, when the row holds a MARK (another sender
    registered it): a registration is never demoted.

    The refusal belongs to one attempt: AADE's answer to the send *invoice*
    made, or, *before_sending*, our own refusal of a document built to
    replace the send the row records (its lookup's *verdict* settles that
    send, :func:`_settle_the_last_send`), which raises
    ``MyDataSendInFlight`` instead while that send may still be running, as
    a claim over it does. It is written only while the row
    names that attempt (``mydata_sent_at``): a send another sender began
    since owns the row's outcome, and this one is only raised.

    Error 228 answers that AADE holds the document under its uid, which
    decides every send of it (``mydata_unanswered_since`` is cleared; its
    MARK is recovered next). Any other refusal is of one document's content
    and says nothing of an earlier send AADE did not answer, which stays
    undecided until a lookup decides it."""
    from order.models.invoice import MyDataStatus

    with transaction.atomic():
        locked = (
            type(invoice)
            ._default_manager.select_for_update()
            .get(pk=invoice.pk)
        )
        if locked.mydata_mark:
            _mirror_registration(invoice, locked)
            return False
        if before_sending and send_in_flight(locked, at=timezone.now()):
            # The send it would replace may still be running: refusing a
            # document built over it would end the guard that keeps every
            # other sender off that send.
            raise _in_flight_error(locked)
        fields = []
        if code == ERROR_DUPLICATE_UID and locked.mydata_unanswered_since:
            locked.mydata_unanswered_since = None
            fields.append("mydata_unanswered_since")
        if locked.mydata_sent_at == invoice.mydata_sent_at:
            if before_sending:
                fields += _settle_the_last_send(locked, verdict)
            locked.mydata_status = MyDataStatus.REJECTED
            locked.mydata_error_code = code[:10]
            locked.mydata_error_message = message or ""
            fields += [
                "mydata_status",
                "mydata_error_code",
                "mydata_error_message",
                "updated_at",
            ]
        if fields:
            locked.save(update_fields=fields)
    if locked.mydata_sent_at == invoice.mydata_sent_at:
        invoice.mydata_status = MyDataStatus.REJECTED
        invoice.mydata_error_code = code[:10]
        invoice.mydata_error_message = message or ""
    invoice.mydata_unanswered_since = locked.mydata_unanswered_since
    return True


def _persist_error_only(invoice: Any, *, code: str, message: str) -> None:
    """Record an error on the invoice WITHOUT changing ``mydata_status``.

    Used when a CONFIRMED invoice's cancellation fails: the document is still
    validly transmitted at AADE, so it must not be flipped to REJECTED
    (G0262). A row canceled meanwhile keeps no error: its cancellation
    stands, whatever a later attempt was told.
    """
    from order.models.invoice import MyDataStatus

    with transaction.atomic():
        locked = (
            type(invoice)
            ._default_manager.select_for_update()
            .get(pk=invoice.pk)
        )
        if locked.mydata_status == MyDataStatus.CANCELED:
            return
        locked.mydata_error_code = code[:10]
        locked.mydata_error_message = message or ""
        locked.save(
            update_fields=[
                "mydata_error_code",
                "mydata_error_message",
                "updated_at",
            ]
        )
    invoice.mydata_error_code = code[:10]
    invoice.mydata_error_message = message or ""


def _persist_cancellation(invoice: Any, *, cancellation_mark: int) -> None:
    from order.models.invoice import MyDataStatus

    with transaction.atomic():
        locked = (
            type(invoice)
            ._default_manager.select_for_update()
            .get(pk=invoice.pk)
        )
        locked.mydata_status = MyDataStatus.CANCELED
        locked.mydata_cancellation_mark = cancellation_mark
        locked.save(
            update_fields=[
                "mydata_status",
                "mydata_cancellation_mark",
                "updated_at",
            ]
        )
    invoice.mydata_status = MyDataStatus.CANCELED
    invoice.mydata_cancellation_mark = cancellation_mark


def _persist_recovered_mark(invoice: Any, registered: TransmittedDoc) -> None:
    """Flip invoice to CONFIRMED with the MARK of the document
    ``RequestTransmittedDocs`` listed under its uid (*registered*), the
    same locked pattern as ``_persist_success``. What the listing says of
    the registered document is kept: AADE's verification URL
    (``qrCodeUrl``, when it lists one), and the movement's start as AADE
    holds it (``dispatchDate``): the send that registered it may not be
    the one whose block the row holds (one redated after an ``ABSENT``
    lookup while an earlier send was still on its way). The listing
    carries no authentication code. A row that holds a MARK already
    (registered, or canceled in myDATA since) is left as it is."""
    from order.models.invoice import MyDataStatus

    with transaction.atomic():
        locked = (
            type(invoice)
            ._default_manager.select_for_update()
            .get(pk=invoice.pk)
        )
        if locked.mydata_mark:
            _mirror_registration(invoice, locked)
            return
        locked.mydata_status = MyDataStatus.CONFIRMED
        locked.mydata_mark = registered.invoice_mark
        locked.mydata_confirmed_at = timezone.now()
        locked.mydata_error_code = ""
        locked.mydata_error_message = ""
        locked.mydata_unanswered_since = None
        fields = [
            "mydata_status",
            "mydata_mark",
            "mydata_confirmed_at",
            "mydata_error_code",
            "mydata_error_message",
            "mydata_unanswered_since",
            "updated_at",
        ]
        if registered.qr_code_url:
            locked.mydata_qr_url = registered.qr_code_url
            fields.append("mydata_qr_url")
        stored = getattr(locked, "movement", None)
        if stored and registered.dispatch_date is not None:
            issued = ReceiptMovement.from_json(stored)
            if issued.dispatch_date != registered.dispatch_date:
                locked.movement = replace(
                    issued, dispatch_date=registered.dispatch_date
                ).as_json()
                fields.append("movement")
        _drop_the_unregistered_pdf(locked, fields)
        locked.save(update_fields=fields)
    invoice.mydata_status = MyDataStatus.CONFIRMED
    invoice.mydata_mark = registered.invoice_mark
    invoice.mydata_qr_url = locked.mydata_qr_url
    invoice.document_file = locked.document_file.name or ""
    if stored:
        invoice.movement = locked.movement
