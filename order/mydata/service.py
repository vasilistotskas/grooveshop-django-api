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
- Error 228 (duplicate uid) becomes ``MyDataDuplicateError`` — the
  caller can opt to recover via ``RequestTransmittedDocs`` in a later
  iteration (not implemented yet — logs + marks ``CONFIRMED`` with
  the original MARK will be Tier A.5).
"""

from __future__ import annotations

import logging
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
    MyDataValidationError,
)
from order.mydata.parser import ResponseRow, parse_response_doc
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
        # Recovery path: AADE already has this uid registered under
        # another MARK. Tier A.5 will query RequestTransmittedDocs and
        # write back that MARK so the invoice ends up CONFIRMED. For
        # now we surface it as a distinct exception so the caller can
        # log loud and schedule manual reconciliation.
        _persist_failure(invoice, code=code, message=message)
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
