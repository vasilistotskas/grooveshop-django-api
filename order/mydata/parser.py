"""Parse AADE ``ResponseDoc`` XML into typed Python structures.

Per AADE v1.0.10 §6.1, every submission endpoint (``SendInvoices``,
``CancelInvoice``, classification endpoints) returns a ``ResponseDoc``
containing one ``<response>`` per submitted entity. Each row carries
``statusCode`` plus either MARK fields (on success) or ``errors``
(on failure) — and the whole thing can be partially successful
(some rows succeed, some don't), which :func:`parse_response_doc`
surfaces via per-row ``status``.

Status codes per the spec: ``Success``, ``ValidationError``,
``TechnicalError``, ``XMLSyntaxError``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from xml.etree.ElementTree import Element, fromstring

StatusCode = Literal[
    "Success", "ValidationError", "TechnicalError", "XMLSyntaxError"
]


@dataclass(frozen=True)
class MyDataError:
    """One ``ErrorType`` row inside a non-success response."""

    code: str
    message: str


@dataclass(frozen=True)
class ResponseRow:
    """One ``<response>`` element in the ``ResponseDoc``.

    At most one of ``invoice_mark`` / ``cancellation_mark`` /
    ``classification_mark`` is populated, reflecting the submission
    kind. ``qr_url`` is populated only on successful invoice rows
    of types 1.1–11.5 (per AADE v1.0.10 §6.1 note 9).
    """

    index: int
    status_code: StatusCode
    invoice_uid: str = ""
    invoice_mark: int | None = None
    cancellation_mark: int | None = None
    classification_mark: int | None = None
    authentication_code: str = ""
    qr_url: str = ""
    errors: list[MyDataError] = field(default_factory=list)

    @property
    def is_success(self) -> bool:
        return self.status_code == "Success"


@dataclass(frozen=True)
class ResponseDoc:
    """Parsed ``ResponseDoc`` — a list of :class:`ResponseRow`."""

    rows: list[ResponseRow]

    def first(self) -> ResponseRow:
        """Convenience for single-entity submissions. Raises
        ``IndexError`` when the response is empty — treat as a bug."""
        return self.rows[0]


def parse_response_doc(xml_bytes: bytes) -> ResponseDoc:
    """Parse the ``ResponseDoc`` XML returned by any AADE submission.

    Namespace-tolerant — AADE returns the doc sometimes bare,
    sometimes under ``xmlns="http://www.aade.gr/..."``. We strip the
    namespace prefix before matching so both forms work.
    """
    root = fromstring(xml_bytes)
    rows = [_parse_row(el) for el in _iter_children(root, "response")]
    return ResponseDoc(rows=rows)


def _parse_row(el: Element) -> ResponseRow:
    status_code = _text(el, "statusCode") or "TechnicalError"
    index = int(_text(el, "index") or "0")
    errors = [
        MyDataError(
            code=_text(err_el, "code") or "",
            message=_text(err_el, "message") or "",
        )
        for err_el in _iter_children(el, "errors")
    ]
    return ResponseRow(
        index=index,
        status_code=status_code,  # type: ignore[arg-type]
        invoice_uid=_text(el, "invoiceUid") or "",
        invoice_mark=_int_or_none(_text(el, "invoiceMark")),
        cancellation_mark=_int_or_none(_text(el, "cancellationMark")),
        classification_mark=_int_or_none(_text(el, "classificationMark")),
        authentication_code=_text(el, "authenticationCode") or "",
        qr_url=_text(el, "qrUrl") or "",
        errors=errors,
    )


def _iter_children(el: Element, local_name: str):
    for child in el:
        if _local(child.tag) == local_name:
            yield child


def _text(el: Element, local_name: str) -> str | None:
    for child in el:
        if _local(child.tag) == local_name:
            return (child.text or "").strip() or None
    return None


def _local(tag: str) -> str:
    """Strip the XML namespace prefix from a tag name."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _int_or_none(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None
