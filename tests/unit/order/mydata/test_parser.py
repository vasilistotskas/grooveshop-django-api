"""Unit tests for :func:`order.mydata.parser.parse_response_doc`.

Pins the parser's handling of success, partial success (multiple
rows), ``ValidationError`` with errors list, and namespace-tagged
responses — AADE sometimes wraps ``ResponseDoc`` in a default
namespace and the parser must stay tolerant of both shapes.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from order.mydata.parser import parse_response_doc


_SUCCESS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ResponseDoc>
    <response>
        <index>1</index>
        <statusCode>Success</statusCode>
        <invoiceUid>abcdef0123456789abcdef0123456789abcdef01</invoiceUid>
        <invoiceMark>800000165789545</invoiceMark>
        <qrUrl>https://www1.aade.gr/mydata/qr/abc</qrUrl>
    </response>
</ResponseDoc>
"""

_VALIDATION_ERROR_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ResponseDoc>
    <response>
        <index>1</index>
        <statusCode>ValidationError</statusCode>
        <errors>
            <code>102</code>
            <message>Application VAT number does not belong to active corporation</message>
        </errors>
    </response>
</ResponseDoc>
"""

_NAMESPACED_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ResponseDoc xmlns="http://www.aade.gr/myDATA/response/v1.0">
    <response>
        <index>1</index>
        <statusCode>Success</statusCode>
        <invoiceMark>800000165789545</invoiceMark>
    </response>
</ResponseDoc>
"""

_PARTIAL_SUCCESS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ResponseDoc>
    <response>
        <index>1</index>
        <statusCode>Success</statusCode>
        <invoiceMark>800000165789545</invoiceMark>
    </response>
    <response>
        <index>2</index>
        <statusCode>ValidationError</statusCode>
        <errors>
            <code>216</code>
            <message>vatCategory must have value other than 8</message>
        </errors>
    </response>
</ResponseDoc>
"""

_CANCELLATION_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<ResponseDoc>
    <response>
        <index>1</index>
        <statusCode>Success</statusCode>
        <cancellationMark>900000000000001</cancellationMark>
    </response>
</ResponseDoc>
"""


class ParseResponseDocTestCase(SimpleTestCase):
    def test_parses_successful_send_invoice(self):
        doc = parse_response_doc(_SUCCESS_XML)
        self.assertEqual(len(doc.rows), 1)
        row = doc.first()
        self.assertTrue(row.is_success)
        self.assertEqual(row.invoice_mark, 800000165789545)
        self.assertEqual(
            row.invoice_uid, "abcdef0123456789abcdef0123456789abcdef01"
        )
        self.assertEqual(row.qr_url, "https://www1.aade.gr/mydata/qr/abc")
        self.assertEqual(row.errors, [])

    def test_parses_validation_error_with_code_and_message(self):
        doc = parse_response_doc(_VALIDATION_ERROR_XML)
        row = doc.first()
        self.assertFalse(row.is_success)
        self.assertEqual(row.status_code, "ValidationError")
        self.assertEqual(len(row.errors), 1)
        self.assertEqual(row.errors[0].code, "102")
        self.assertIn("active corporation", row.errors[0].message)
        self.assertIsNone(row.invoice_mark)

    def test_tolerates_default_namespace(self):
        """AADE sometimes wraps the response in
        ``xmlns="http://www.aade.gr/..."``. The parser must strip the
        namespace prefix instead of silently returning zero rows."""
        doc = parse_response_doc(_NAMESPACED_XML)
        self.assertEqual(len(doc.rows), 1)
        self.assertEqual(doc.first().invoice_mark, 800000165789545)

    def test_partial_success_surfaces_per_row_status(self):
        """Batch submissions can have some rows succeed and others
        fail — the parser must preserve per-row status, not collapse
        to a single top-level result."""
        doc = parse_response_doc(_PARTIAL_SUCCESS_XML)
        self.assertEqual(len(doc.rows), 2)
        self.assertTrue(doc.rows[0].is_success)
        self.assertFalse(doc.rows[1].is_success)
        self.assertEqual(doc.rows[1].errors[0].code, "216")

    def test_parses_cancellation_mark(self):
        doc = parse_response_doc(_CANCELLATION_XML)
        row = doc.first()
        self.assertTrue(row.is_success)
        self.assertEqual(row.cancellation_mark, 900000000000001)
        self.assertIsNone(row.invoice_mark)

    def test_empty_response_doc_returns_synthetic_error_row(self):
        """Regression: AADE gateway sometimes returns HTTP 200 with
        an empty ``<ResponseDoc/>`` (auth / routing issues). The
        parser used to raise ``IndexError`` from ``first()``, which
        crashed the Celery task and left the invoice zombied in
        SUBMITTED state. Now it returns a TechnicalError row so the
        service-layer classifier treats it as a (retryable)
        transport fault rather than crashing."""
        empty = b'<?xml version="1.0" encoding="UTF-8"?><ResponseDoc/>'
        doc = parse_response_doc(empty)
        self.assertEqual(doc.rows, [])
        # ``first()`` must NOT raise — returns a synthetic error row.
        row = doc.first()
        self.assertEqual(row.status_code, "TechnicalError")
        self.assertFalse(row.is_success)
        self.assertEqual(len(row.errors), 1)
