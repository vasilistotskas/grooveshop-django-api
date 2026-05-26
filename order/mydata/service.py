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
  ``recover_mark_for_invoice`` queries ``RequestTransmittedDocs`` and
  writes the existing MARK back if exactly one matching doc is found.
  If recovery fails for any reason the invoice stays ``REJECTED`` and
  the caller falls back to the existing manual-reconciliation path.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.db import transaction
from django.utils import timezone

from extra_settings.models import Setting

from order.mydata.builder import build_invoice_xml
from order.mydata.client import MyDataClient
from order.mydata.config import load_config
from order.mydata.exceptions import (
    MyDataDuplicateError,
    MyDataInactiveCounterpartError,
    MyDataTransportError,
    MyDataValidationError,
)
from order.mydata.parser import (
    ResponseRow,
    parse_requested_doc,
    parse_response_doc,
)
from order.mydata.types import ERROR_DUPLICATE_UID, ERROR_INACTIVE_VAT
from order.mydata.validator import validate_invoice_doc

logger = logging.getLogger(__name__)


def submit_invoice(invoice: Any) -> ResponseRow | None:
    """Submit ``invoice`` to AADE ``SendInvoices``.

    Returns the parsed :class:`ResponseRow` on success (MARK + qrUrl
    persisted on the invoice). Returns ``None`` when myDATA is not
    enabled / not configured — callers treat this as "integration off",
    NOT as failure.

    Raises one of the :class:`MyDataError` subclasses when the
    submission definitively failed.
    """
    config = load_config()
    if not config.is_ready():
        logger.debug(
            "myDATA integration not ready (enabled=%s, creds present=%s). "
            "Skipping submit for invoice %s.",
            config.enabled,
            bool(config.user_id and config.subscription_key),
            invoice.invoice_number,
        )
        return None

    issuer_vat = _resolve_issuer_vat()
    if not issuer_vat:
        raise MyDataValidationError(
            "INVOICE_SELLER_VAT_ID is empty — cannot submit to myDATA. "
            "Fill it in via Settings admin first.",
            code="MISSING_SELLER_VAT",
        )

    _guard_b2b_invoice_integrity(invoice)

    try:
        built = build_invoice_xml(
            invoice,
            issuer_vat=issuer_vat,
            issuer_country="GR",
            branch=config.issuer_branch,
            series_prefix=config.invoice_series_prefix,
        )
    except ValueError as exc:
        # Unsupported VAT rate or malformed invoice_number — bad
        # master data, not transient. Record as a terminal validation
        # failure so the admin UI surfaces the reason rather than
        # retrying 5 times and then burying the error.
        _persist_failure(
            invoice,
            code="BUILD",
            message=f"Failed to build myDATA payload: {exc}",
        )
        raise MyDataValidationError(str(exc), code="BUILD") from exc

    try:
        validate_invoice_doc(built.xml_bytes)
    except Exception as exc:  # noqa: BLE001 — xmlschema raises many subtypes
        # XSD validation error is terminal: the payload does not
        # match the pinned schema. Fall into the same rejection
        # bucket as a server-side ValidationError so ops can see
        # the reason in the admin instead of a zombie SUBMITTED row.
        _persist_failure(
            invoice, code="XSD", message=f"Local XSD validation failed: {exc}"
        )
        raise MyDataValidationError(str(exc), code="XSD") from exc

    # Persist UID + request-scope identity BEFORE the HTTP call so a
    # transport failure leaves enough state to recover on retry.
    _persist_submission_intent(
        invoice,
        uid=built.uid,
        invoice_type=built.invoice_type,
        series=built.series,
        aa=built.aa,
    )

    client = MyDataClient(config)
    response_bytes = client.send_invoices(built.xml_bytes)
    response = parse_response_doc(response_bytes)
    row = response.first()

    if row.is_success:
        _persist_success(invoice, row)
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
        # a MARK from a prior transmission whose response we never
        # received. Persist the error first (leaves REJECTED + uid set
        # so recover_mark_for_invoice can use the uid), then attempt to
        # recover the MARK via RequestTransmittedDocs.
        _persist_failure(invoice, code=code, message=message)
        recovered = recover_mark_for_invoice(invoice)
        if recovered:
            # Return a synthetic success row so the caller treats this
            # as CONFIRMED and chains the post-submission steps (PDF
            # re-render + email delivery). We don't have a real
            # ResponseRow from AADE, so synthesise one that carries
            # only the fields _persist_recovered_mark wrote.
            from order.mydata.parser import ResponseRow as _ResponseRow

            return _ResponseRow(
                index=0,
                status_code="Success",
                invoice_uid=invoice.mydata_uid or "",
                invoice_mark=invoice.mydata_mark,
            )
        raise MyDataDuplicateError(message, code=code)

    if code == ERROR_INACTIVE_VAT:
        # Buyer ΑΦΜ is not in AADE's active registry. Customer-fixable
        # (re-enter the number), so surface a distinct exception so
        # the task can queue a tailored "your VAT ID is invalid"
        # email instead of the generic rejection notice.
        _persist_failure(invoice, code=code, message=message)
        raise MyDataInactiveCounterpartError(message, code=code)

    _persist_failure(invoice, code=code, message=message)
    raise MyDataValidationError(message, code=code)


def cancel_invoice(invoice: Any) -> ResponseRow | None:
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
    _persist_failure(invoice, code=code, message=message)
    raise MyDataValidationError(message, code=code)


def recover_mark_for_invoice(invoice: Any) -> bool:
    """Attempt to recover a MARK for an invoice that received error 228.

    Error 228 means AADE already has our ``uid`` registered under a MARK
    (from a prior successful transmission whose response we never
    received). This function calls ``RequestTransmittedDocs`` scoped to
    the invoice's issue date ± 1 day and our entity VAT, then finds the
    single transmitted doc whose ``uid`` matches ``invoice.mydata_uid``.

    SAFETY CONTRACT — the MARK is only written when ALL of:
      1. Exactly one transmitted doc matches the invoice's uid.
      2. That doc has a non-None ``invoiceMark``.
    If zero docs match, more than one match, or the matched doc has no
    MARK, this function returns ``False`` and writes nothing. A wrong
    MARK must be impossible.

    Returns ``True`` when the MARK was successfully recovered and the
    invoice has been flipped to ``CONFIRMED``.
    Returns ``False`` in every other case:
      - config not ready
      - no uid to look up
      - transport error during the query
      - zero matches
      - multiple matches (uid collision — should never happen)
      - match found but invoiceMark is None

    The caller (``send_invoice_to_mydata`` task) keeps the existing
    ``REJECTED`` state and manual-reconciliation path when this returns
    ``False``.
    """
    uid_to_find = getattr(invoice, "mydata_uid", "") or ""
    if not uid_to_find:
        logger.warning(
            "recover_mark_for_invoice: invoice pk=%s has no mydata_uid "
            "— cannot query RequestTransmittedDocs",
            invoice.pk,
        )
        return False

    config = load_config()
    if not config.is_ready():
        logger.debug(
            "recover_mark_for_invoice: myDATA not ready, skipping recovery "
            "for invoice pk=%s",
            invoice.pk,
        )
        return False

    issuer_vat = _resolve_issuer_vat()
    if not issuer_vat:
        logger.warning(
            "recover_mark_for_invoice: INVOICE_SELLER_VAT_ID is empty, "
            "cannot scope RequestTransmittedDocs query for invoice pk=%s",
            invoice.pk,
        )
        return False

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
        return False

    date_from = issue_date - timedelta(days=1)
    date_to = issue_date + timedelta(days=1)

    client = MyDataClient(config)
    all_docs = []

    # Follow pagination until exhausted. In practice a ±1-day window
    # scoped to our own VAT should return at most a handful of docs
    # (one per order that day), so pagination is extremely unlikely —
    # but we implement it for correctness.
    next_partition_key: str | None = None
    next_row_key: str | None = None
    max_pages = 20  # hard ceiling against infinite loops on bad responses
    for _ in range(max_pages):
        try:
            raw = client.request_transmitted_docs(
                entity_vat_number=issuer_vat,
                date_from=date_from,
                date_to=date_to,
                next_partition_key=next_partition_key,
                next_row_key=next_row_key,
            )
        except MyDataTransportError as exc:
            logger.warning(
                "recover_mark_for_invoice: transport error querying "
                "RequestTransmittedDocs for invoice pk=%s uid=%s: %s",
                invoice.pk,
                uid_to_find,
                exc,
            )
            # Do NOT re-raise — caller keeps REJECTED + logs.
            return False

        result = parse_requested_doc(raw)
        all_docs.extend(result.docs)

        if result.next_partition_key is None and result.next_row_key is None:
            break
        next_partition_key = result.next_partition_key
        next_row_key = result.next_row_key

    # SAFETY: require exactly one match for our uid.
    matches = [d for d in all_docs if d.uid == uid_to_find]

    if len(matches) == 0:
        logger.warning(
            "recover_mark_for_invoice: uid=%s not found in "
            "RequestTransmittedDocs window %s–%s for invoice pk=%s "
            "(retrieved %d docs). Keeping REJECTED state.",
            uid_to_find,
            date_from,
            date_to,
            invoice.pk,
            len(all_docs),
        )
        return False

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
        return False

    matched = matches[0]
    if matched.invoice_mark is None:
        logger.warning(
            "recover_mark_for_invoice: uid=%s matched but invoiceMark "
            "is None for invoice pk=%s. Keeping REJECTED state.",
            uid_to_find,
            invoice.pk,
        )
        return False

    # All safety checks passed — write the recovered MARK.
    logger.info(
        "recover_mark_for_invoice: recovered MARK=%s for invoice pk=%s "
        "uid=%s via RequestTransmittedDocs",
        matched.invoice_mark,
        invoice.pk,
        uid_to_find,
    )
    _persist_recovered_mark(invoice, mark=matched.invoice_mark)
    return True


def _guard_b2b_invoice_integrity(invoice: Any) -> None:
    """Reject the submission early if the order-level document-type
    and the per-Order billing-VAT fields disagree.

    ``Order.document_type == INVOICE`` is the merchant-facing signal
    that the buyer asked for a proper Τιμολόγιο Πώλησης. Without a
    ``billing_vat_id`` we'd silently fall back to 11.1 (retail
    receipt), which is tax-fraud-adjacent. Fail loud here so the
    admin sees a REJECTED row with a clear message instead of an
    11.1 submission that "quietly worked" under the wrong type.

    The API serializer enforces the same rule so this branch only
    fires when a bad row slips past (e.g. legacy order rows, or an
    admin bulk edit that flipped ``document_type``)."""
    from order.enum.document_type import OrderDocumentTypeEnum

    order = invoice.order
    doc_type = getattr(order, "document_type", None)
    if doc_type != OrderDocumentTypeEnum.INVOICE.value:
        return
    buyer_vat = (getattr(order, "billing_vat_id", "") or "").strip()
    if not buyer_vat:
        _persist_failure(
            invoice,
            code="MISSING_BUYER_VAT",
            message=(
                "Order is marked as INVOICE but no billing_vat_id is set. "
                "Either populate the buyer VAT (for B2B) or change the "
                "document_type to RECEIPT."
            ),
        )
        raise MyDataValidationError(
            "Order requests an invoice but has no buyer VAT.",
            code="MISSING_BUYER_VAT",
        )


def _resolve_issuer_vat() -> str:
    """Read the seller VAT from extra_settings. Kept separate so
    tests can monkey-patch without spinning the whole config stack."""
    return str(Setting.get("INVOICE_SELLER_VAT_ID", default="") or "")


def _persist_submission_intent(
    invoice: Any,
    *,
    uid: str,
    invoice_type: str,
    series: str,
    aa: int,
) -> None:
    """Lock + save the identity fields before the HTTP round-trip."""
    from order.models.invoice import Invoice, MyDataStatus

    with transaction.atomic():
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        locked.mydata_uid = uid
        locked.mydata_invoice_type = invoice_type
        locked.mydata_series = series
        locked.mydata_aa = aa
        locked.mydata_status = MyDataStatus.SUBMITTED
        if locked.mydata_submitted_at is None:
            locked.mydata_submitted_at = timezone.now()
        locked.mydata_error_code = ""
        locked.mydata_error_message = ""
        locked.save(
            update_fields=[
                "mydata_uid",
                "mydata_invoice_type",
                "mydata_series",
                "mydata_aa",
                "mydata_status",
                "mydata_submitted_at",
                "mydata_error_code",
                "mydata_error_message",
                "updated_at",
            ]
        )
    # Mirror onto the in-memory instance so the caller sees the
    # updated fields without re-fetching.
    invoice.mydata_uid = uid
    invoice.mydata_invoice_type = invoice_type
    invoice.mydata_series = series
    invoice.mydata_aa = aa
    invoice.mydata_status = MyDataStatus.SUBMITTED


def _persist_success(invoice: Any, row: ResponseRow) -> None:
    from order.models.invoice import Invoice, MyDataStatus

    with transaction.atomic():
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        locked.mydata_status = MyDataStatus.CONFIRMED
        locked.mydata_mark = row.invoice_mark
        locked.mydata_authentication_code = row.authentication_code or ""
        locked.mydata_qr_url = row.qr_url or ""
        locked.mydata_confirmed_at = timezone.now()
        locked.mydata_error_code = ""
        locked.mydata_error_message = ""
        locked.save(
            update_fields=[
                "mydata_status",
                "mydata_mark",
                "mydata_authentication_code",
                "mydata_qr_url",
                "mydata_confirmed_at",
                "mydata_error_code",
                "mydata_error_message",
                "updated_at",
            ]
        )
    invoice.mydata_status = MyDataStatus.CONFIRMED
    invoice.mydata_mark = row.invoice_mark
    invoice.mydata_qr_url = row.qr_url or ""


def _persist_failure(invoice: Any, *, code: str, message: str) -> None:
    from order.models.invoice import Invoice, MyDataStatus

    with transaction.atomic():
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        locked.mydata_status = MyDataStatus.REJECTED
        locked.mydata_error_code = code[:10]
        locked.mydata_error_message = message or ""
        locked.save(
            update_fields=[
                "mydata_status",
                "mydata_error_code",
                "mydata_error_message",
                "updated_at",
            ]
        )
    invoice.mydata_status = MyDataStatus.REJECTED
    invoice.mydata_error_code = code[:10]
    invoice.mydata_error_message = message or ""


def _persist_cancellation(invoice: Any, *, cancellation_mark: int) -> None:
    from order.models.invoice import Invoice, MyDataStatus

    with transaction.atomic():
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
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


def _persist_recovered_mark(invoice: Any, *, mark: int) -> None:
    """Flip invoice to CONFIRMED with a MARK recovered via
    ``RequestTransmittedDocs``. Uses the same locked pattern as
    ``_persist_success`` — no qr_url or authentication_code because
    those aren't returned by the query endpoint."""
    from order.models.invoice import Invoice, MyDataStatus

    with transaction.atomic():
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        locked.mydata_status = MyDataStatus.CONFIRMED
        locked.mydata_mark = mark
        locked.mydata_confirmed_at = timezone.now()
        locked.mydata_error_code = ""
        locked.mydata_error_message = ""
        locked.save(
            update_fields=[
                "mydata_status",
                "mydata_mark",
                "mydata_confirmed_at",
                "mydata_error_code",
                "mydata_error_message",
                "updated_at",
            ]
        )
    invoice.mydata_status = MyDataStatus.CONFIRMED
    invoice.mydata_mark = mark
