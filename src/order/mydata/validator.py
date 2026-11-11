"""Optional XSD pre-validation for outbound payloads.

Validation is a **fail-fast local check** — AADE does its own
server-side validation on every submission so the integration is
correct even without this. The upside of pre-validation is faster
feedback when master data or builder code drifts: we catch schema
errors before paying the round-trip cost.

Schema sourcing is the operator's job: drop the official AADE XSDs
into :const:`XSD_DIR`. They're hosted behind auth on the dev portal
(``mydata-dev.azure-api.net``) — we can't redistribute them. When the
directory is empty, :func:`validate_invoice_doc` is a no-op (logs an
info line and returns).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

XSD_DIR = Path(__file__).parent / "xsd"
INVOICE_SCHEMA_FILENAME = "InvoicesDoc-v1.0.10.xsd"


def validate_invoice_doc(xml_bytes: bytes) -> None:
    """Validate an ``InvoicesDoc`` payload against the pinned XSD.

    Raises ``xmlschema.XMLSchemaValidationError`` on failure —
    caller converts to a :class:`MyDataValidationError`. No-op when
    the XSD file isn't present (ops hasn't dropped it in yet).
    """
    schema_path = XSD_DIR / INVOICE_SCHEMA_FILENAME
    if not schema_path.is_file():
        logger.info(
            "Skipping myDATA XSD pre-validation — schema not found at %s. "
            "Drop the official AADE XSDs into order/mydata/xsd/ to enable.",
            schema_path,
        )
        return

    schema = _load_schema(schema_path)
    schema.validate(xml_bytes)


_SCHEMA_CACHE: dict[Path, object] = {}


def _load_schema(path: Path):
    """Cache schemas by path — parsing the XSD is expensive and the
    file changes at most once per AADE version bump."""
    cached = _SCHEMA_CACHE.get(path)
    if cached is not None:
        return cached
    import xmlschema

    schema = xmlschema.XMLSchema(str(path))
    _SCHEMA_CACHE[path] = schema
    return schema
