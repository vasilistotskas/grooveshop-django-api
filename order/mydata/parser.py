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
from typing import Literal, cast, get_args
from xml.etree.ElementTree import Element, fromstring

StatusCode = Literal[
    "Success", "ValidationError", "TechnicalError", "XMLSyntaxError"
]

# Runtime whitelist for narrowing arbitrary strings back to the
# Literal. ``get_args(StatusCode)`` keeps this in sync automatically.
_STATUS_CODES: frozenset[str] = frozenset(get_args(StatusCode))


def _coerce_status(raw: str | None) -> StatusCode:
    """Narrow AADE's ``statusCode`` string to our ``StatusCode`` Literal.

    AADE always returns one of the four values, but a malformed /
    partial response could theoretically carry something else — treat
    unknowns as ``TechnicalError`` so the caller reacts conservatively.
    """
    if raw and raw in _STATUS_CODES:
        return cast(StatusCode, raw)
    return "TechnicalError"


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
        """Convenience for single-entity submissions. Synthesises a
        ``TechnicalError`` row with no mark when the response doc is
        empty — happens for some AADE gateway faults (auth issues
        that land as 200 with an empty body). Callers branch on
        ``status_code`` anyway, so this keeps the downstream
        classification logic consistent instead of crashing with
        ``IndexError``."""
        if not self.rows:
            return ResponseRow(
                index=0,
                status_code="TechnicalError",
                errors=[
                    MyDataError(
                        code="",
                        message="AADE returned an empty ResponseDoc",
                    )
                ],
            )
        return self.rows[0]


def parse_response_doc(xml_bytes: bytes) -> ResponseDoc:
    """Parse the ``ResponseDoc`` XML returned by any AADE submission.

    Shape tolerance: AADE's dev endpoint sometimes wraps the real
    ``ResponseDoc`` inside a Microsoft DataContract ``<string>``
    envelope (``xmlns="http://schemas.microsoft.com/2003/10/Serialization/"``)
    with the inner XML HTML-entity-escaped as text. We unwrap that
    form before parsing. The bare and namespaced ``ResponseDoc``
    forms both work via the namespace-stripping in ``_local``.
    """
    root = fromstring(xml_bytes)
    if _local(root.tag) == "string" and root.text:
        # Re-parse the inner (previously escaped) XML. ``root.text``
        # already contains the unescaped string because ElementTree
        # resolves entities on parse.
        root = fromstring(root.text.encode("utf-8"))
    rows = [_parse_row(el) for el in _iter_children(root, "response")]
    return ResponseDoc(rows=rows)


def _parse_row(el: Element) -> ResponseRow:
    status_code = _coerce_status(_text(el, "statusCode"))
    index = int(_text(el, "index") or "0")
    # AADE wraps multiple ``<error>`` rows inside a single
    # ``<errors>`` container (per v1.0.10 §6.1 ErrorType list). Flat
    # test fixtures sometimes inline the ``<code>`` / ``<message>``
    # directly in ``<errors>`` — tolerate both shapes.
    errors: list[MyDataError] = []
    for errors_el in _iter_children(el, "errors"):
        inner_errors = list(_iter_children(errors_el, "error"))
        if inner_errors:
            for err_el in inner_errors:
                errors.append(
                    MyDataError(
                        code=_text(err_el, "code") or "",
                        message=_text(err_el, "message") or "",
                    )
                )
        else:
            # Legacy / flat shape: <errors><code/><message/></errors>
            errors.append(
                MyDataError(
                    code=_text(errors_el, "code") or "",
                    message=_text(errors_el, "message") or "",
                )
            )
    return ResponseRow(
        index=index,
        status_code=status_code,
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
