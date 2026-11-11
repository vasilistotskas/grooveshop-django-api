"""Deterministic UID generation for myDATA submissions.

Per AADE myDATA API Documentation v1.0.10 §5 (p.24), ``uid`` is the
SHA-1 hash over these seven fields, ISO-8859-7 encoded:

    1. issuer VAT (ΑΦΜ Εκδότη)
    2. issue date (Ημερομηνία Έκδοσης)
    3. branch — TAXIS establishment number (Αριθμός Εγκατάστασης)
    4. invoice type (Τύπος Παραστατικού)
    5. series (Σειρά)
    6. aa — serial number (ΑΑ)
    7. deviation type, if present (Τύπος Απόκλισης Παραστατικού)

For invoice categories Β1 / Β2 (document types 13.x, 14.x, 15.1, 16.1
— expense / third-party receipts), the **receiver** VAT also
participates. Those categories are not in scope for Tier A; when
added, pass ``receiver_vat`` and it's appended to the payload.

AADE dedupes by uid: resubmitting the same tuple returns error 228
with the original MARK — which is exactly the idempotency guarantee
we need for Celery retries. This function must therefore stay
deterministic: no timestamps, no random bytes, no ordering drift.
"""

from __future__ import annotations

import hashlib
from datetime import date


def build_uid(
    *,
    issuer_vat: str,
    issue_date: date,
    branch: int,
    invoice_type: str,
    series: str,
    aa: int,
    deviation_type: str = "",
    receiver_vat: str = "",
) -> str:
    """Return the 40-char SHA-1 hex digest of the AADE identity tuple.

    ISO-8859-7 encoding is what the AADE spec calls out — our content
    is ASCII today (VAT numbers, dates, integers, Latin-alphabet
    series) so UTF-8 and ISO-8859-7 produce identical bytes, but
    encoding via ISO-8859-7 keeps us spec-compliant for future series
    names containing Greek characters.
    """
    parts = [
        issuer_vat,
        issue_date.isoformat(),
        str(branch),
        invoice_type,
        series,
        str(aa),
        deviation_type,
    ]
    if receiver_vat:
        parts.append(receiver_vat)
    payload = "".join(parts).encode("iso-8859-7", errors="replace")
    return hashlib.sha1(payload, usedforsecurity=False).hexdigest()
