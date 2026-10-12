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

- :mod:`order.mydata.config` — resolves runtime config from
  ``extra_settings``.
- :mod:`order.mydata.uid` — deterministic request UID generation.
- :mod:`order.mydata.builder` — ``Invoice`` → ``InvoicesDoc`` XML.
- :mod:`order.mydata.client` — HTTP layer with retry/backoff.
- :mod:`order.mydata.parser` — ``ResponseDoc`` / ``RequestedDoc`` →
  Python dataclasses.
- :mod:`order.mydata.validator` — optional XSD pre-validation.

Pinned schema: AADE ``myDATA v1.0.12`` (under ``xsd/``). Schema bumps
are mechanical — drop the new XSDs in and update
``DEFAULT_XSD_VERSION``.
"""

from order.mydata.exceptions import (
    MyDataAuthError,
    MyDataDuplicateError,
    MyDataError,
    MyDataTransportError,
    MyDataValidationError,
)
from order.mydata.service import (
    cancel_document,
    recover_mark_for_document,
    submit_document,
)

__all__ = [
    "MyDataAuthError",
    "MyDataDuplicateError",
    "MyDataError",
    "MyDataTransportError",
    "MyDataValidationError",
    "cancel_document",
    "recover_mark_for_document",
    "submit_document",
]
