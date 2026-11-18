"""XSD validation of outbound payloads.

A **fail-fast local check**: AADE validates every submission itself, and
this catches a payload that drifted from the schema before the round trip.
The schemas are AADE's public ones, vendored under :const:`XSD_DIR` (see
its README); `xmlschema` (a direct dependency) reads them.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

import xmlschema

XSD_DIR = Path(__file__).parent / "xsd"
INVOICE_SCHEMA_FILENAME = "InvoicesDoc-v2.0.2.xsd"


def validate_invoice_doc(xml_bytes: bytes) -> None:
    """Validate an ``InvoicesDoc`` payload against the vendored XSD.

    Raises ``xmlschema.XMLSchemaValidationError`` on failure; the caller
    turns it into a :class:`MyDataValidationError`.
    """
    _invoice_schema().validate(xml_bytes)


@cache
def _invoice_schema() -> xmlschema.XMLSchema:
    """Parsed once per process: the file changes only with an AADE
    version bump."""
    return xmlschema.XMLSchema(str(XSD_DIR / INVOICE_SCHEMA_FILENAME))
