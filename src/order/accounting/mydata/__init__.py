"""Greek IAPR / AADE myDATA integration.

Public surface used by ``order.tasks`` and the admin layer. A
"document" is an :class:`order.models.Invoice` (the order's retail
receipt, 11.1) or an :class:`order.models.RetailCredit` (a retail credit
document, 11.4):

- :func:`submit_document` — serialise the document, POST it to
  ``SendInvoices``, persist the MARK / UID / QR on the row. When AADE
  returns error 228 (duplicate uid), :func:`recover_mark_for_document`
  is called inline to recover the existing MARK via
  ``RequestTransmittedDocs``.
- :func:`cancel_document` — POST ``CancelInvoice``, persist the
  cancellation MARK (for a document issued in error).
- :func:`recover_mark_for_document` — MARK recovery: query
  ``RequestTransmittedDocs`` and flip the document to CONFIRMED when
  exactly one matching uid is found with a valid MARK.
- :class:`MyDataError` and subclasses — typed exceptions distinguishing
  retryable transport errors from terminal validation errors.

Internals:

- :mod:`order.accounting.mydata.config` — resolves runtime config from
  ``extra_settings``.
- :mod:`order.accounting.mydata.uid` — deterministic request UID generation.
- :mod:`order.accounting.mydata.builder` — ``Invoice`` → ``InvoicesDoc`` XML.
- :mod:`order.accounting.mydata.client` — HTTP layer with retry/backoff.
- :mod:`order.accounting.mydata.parser` — ``ResponseDoc`` / ``RequestedDoc`` →
  Python dataclasses.
- :mod:`order.accounting.mydata.validator` — XSD validation of every
  payload before it is sent.

Pinned schema: AADE's public ``myDATA v2.0.2`` XSDs, vendored under
``xsd/`` (its README says how to move to a new version).
"""

from order.accounting.mydata.exceptions import (
    MyDataAuthError,
    MyDataDuplicateError,
    MyDataError,
    MyDataNotListedError,
    MyDataSendInFlight,
    MyDataTransportError,
    MyDataValidationError,
)
from order.accounting.mydata.service import (
    cancel_document,
    recover_mark_for_document,
    submit_document,
)

__all__ = [
    "MyDataAuthError",
    "MyDataDuplicateError",
    "MyDataError",
    "MyDataNotListedError",
    "MyDataSendInFlight",
    "MyDataTransportError",
    "MyDataValidationError",
    "cancel_document",
    "recover_mark_for_document",
    "submit_document",
]
