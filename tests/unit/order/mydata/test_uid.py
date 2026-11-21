"""Unit tests for :func:`order.accounting.mydata.uid.build_uid`.

The UID function is the idempotency anchor of the whole integration —
retries MUST produce the same hash for the same logical invoice, and
distinct invoices MUST produce distinct hashes. These tests pin both
properties so a future "clean up the API" refactor can't silently
break dedupe on AADE's side.
"""

from __future__ import annotations

import hashlib
from datetime import date

from django.test import SimpleTestCase

from order.accounting.mydata.uid import build_uid


class BuildUidTestCase(SimpleTestCase):
    def _baseline_kwargs(self):
        return {
            "issuer_vat": "123456789",
            "issue_date": date(2026, 4, 22),
            "branch": 0,
            "invoice_type": "11.1",
            "series": "GRVP-2026",
            "aa": 1,
        }

    def test_returns_40_char_hex(self):
        uid = build_uid(**self._baseline_kwargs())
        self.assertEqual(len(uid), 40)
        # AADE's own uids are upper-case hex.
        self.assertTrue(all(c in "0123456789ABCDEF" for c in uid))

    def test_deterministic_across_calls(self):
        """Same inputs MUST produce the same uid — the basis of the
        Celery retry idempotency against AADE error 228."""
        kw = self._baseline_kwargs()
        self.assertEqual(build_uid(**kw), build_uid(**kw))

    def test_distinct_aa_produces_distinct_uid(self):
        kw = self._baseline_kwargs()
        uid_a = build_uid(**{**kw, "aa": 1})
        uid_b = build_uid(**{**kw, "aa": 2})
        self.assertNotEqual(uid_a, uid_b)

    def test_distinct_issuer_vat_produces_distinct_uid(self):
        kw = self._baseline_kwargs()
        uid_a = build_uid(**{**kw, "issuer_vat": "111111111"})
        uid_b = build_uid(**{**kw, "issuer_vat": "222222222"})
        self.assertNotEqual(uid_a, uid_b)

    def test_distinct_issue_date_produces_distinct_uid(self):
        kw = self._baseline_kwargs()
        uid_a = build_uid(**{**kw, "issue_date": date(2026, 4, 22)})
        uid_b = build_uid(**{**kw, "issue_date": date(2026, 4, 23)})
        self.assertNotEqual(uid_a, uid_b)

    def test_receiver_vat_participates_when_provided(self):
        """B1/B2 categories (13.x, 14.x, 15.1, 16.1) fold the
        receiver VAT into the hash — must change the uid."""
        kw = self._baseline_kwargs()
        uid_without = build_uid(**kw)
        uid_with = build_uid(**{**kw, "receiver_vat": "987654321"})
        self.assertNotEqual(uid_without, uid_with)

    def test_matches_the_uid_aade_computes(self):
        """Pin the exact hash so a refactor that changes the encoding or
        join fails loudly. AADE joins the fields with ``-`` and answers in
        upper-case hex: the form was read off the ``invoiceUid`` its
        sandbox returned for a receipt (2026-11-20). The concatenation
        without separators this replaced never matched what AADE lists,
        so no MARK could be looked up by its uid."""
        self.assertEqual(
            build_uid(**self._baseline_kwargs()),
            hashlib.sha1(
                b"123456789-2026-04-22-0-11.1-GRVP-2026-1",
                usedforsecurity=False,
            )
            .hexdigest()
            .upper(),
        )
        self.assertEqual(
            build_uid(**self._baseline_kwargs()),
            "391805A15D31A1DC5C63140D6ABED1229BF89885",
        )
