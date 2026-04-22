"""Greek IAPR / AADE myDATA integration.

Public surface used by ``order.tasks`` and the admin layer:

- :func:`submit_invoice` — serialise an :class:`Invoice`, POST it to
  ``SendInvoices``, persist the MARK / UID / QR on the row.
- :func:`cancel_invoice` — POST ``CancelInvoice``, persist the
  cancellation MARK.
- :class:`MyDataError` and subclasses — typed exceptions distinguishing
  retryable transport errors from terminal validation errors.

Internals:

- :mod:`order.mydata.config` — resolves runtime config from
  ``extra_settings``.
- :mod:`order.mydata.uid` — deterministic request UID generation.
- :mod:`order.mydata.builder` — ``Invoice`` → ``InvoicesDoc`` XML.
- :mod:`order.mydata.client` — HTTP layer with retry/backoff.
- :mod:`order.mydata.parser` — ``ResponseDoc`` → Python dataclass.
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
from order.mydata.service import cancel_invoice, submit_invoice

__all__ = [
    "MyDataAuthError",
    "MyDataDuplicateError",
    "MyDataError",
    "MyDataTransportError",
    "MyDataValidationError",
    "cancel_invoice",
    "submit_invoice",
]
