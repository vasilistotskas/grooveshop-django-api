"""Test for SearchQuery PII retention (G0342)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from search.models import SearchQuery
from search.tasks import anonymize_old_search_queries


@pytest.mark.django_db
def test_anonymize_old_search_queries_strips_pii_after_window():
    old = SearchQuery.objects.create(
        query="old",
        content_type="product",
        results_count=1,
        estimated_total_hits=1,
        ip_address="203.0.113.5",
        user_agent="agent",
        session_key="sess-1",
    )
    SearchQuery.objects.filter(pk=old.pk).update(
        timestamp=timezone.now() - timedelta(days=120)
    )

    recent = SearchQuery.objects.create(
        query="recent",
        content_type="product",
        results_count=1,
        estimated_total_hits=1,
        ip_address="203.0.113.9",
        user_agent="agent2",
        session_key="sess-2",
    )

    scrubbed = anonymize_old_search_queries(days=90)

    assert scrubbed == 1
    old.refresh_from_db()
    recent.refresh_from_db()

    # Old row: identifiers nulled, query text kept for aggregate value.
    assert old.ip_address is None
    assert old.user_agent == ""
    assert old.session_key is None
    assert old.query == "old"

    # Recent row: untouched.
    assert recent.ip_address == "203.0.113.9"
    assert recent.session_key == "sess-2"
