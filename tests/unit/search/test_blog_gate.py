"""Blog search must respect the tenant's blog plan flag.

Every blog VIEWSET chains `IsBlogEnabled`, which 404s when the plan flag
is off. The two search endpoints that serve the same content did not —
and indexing does not know about the flag either
(`BlogPostTranslation.meili_filter` gates on `is_published` alone), so
the documents sit in Meilisearch regardless. Search was the way around
the gate.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db


class _BlogOffTenant:
    """A tenant whose plan has the blog switched off."""

    blog_enabled = False
    schema_name = "blog-off"


def test_blog_search_is_refused_when_the_plan_disallows_it():
    # Drive the REAL permission class through a tenant with the flag
    # off, so the 404 comes from the code under test rather than from a
    # patched-out `has_permission`.
    with patch(
        "tenant.membership.get_current_tenant", return_value=_BlogOffTenant()
    ):
        response = APIClient().get(
            reverse("search-blog-post"), {"query": "anything"}
        )
    # 404, not 403 — `IsBlogEnabled` hides the route's existence rather
    # than advertising a plan tier. Same shape every blog viewset gives.
    assert response.status_code == 404, response.status_code


def test_federated_search_drops_the_blog_index_but_still_serves_products():
    """Refusing the whole endpoint would take product search down too."""
    from search import views as search_views

    captured = {}

    class _FakeClient:
        def multi_search(self, queries, federation):
            captured["queries"] = queries
            return {"hits": [], "estimatedTotalHits": 0}

    with (
        patch.object(
            search_views, "tenant_plan_allows", side_effect=lambda f: False
        ),
        patch.object(
            search_views.meili_client,
            "search_client_for_schema",
            return_value=_FakeClient(),
        ),
    ):
        response = APIClient().get(
            reverse("search-federated"), {"query": "anything"}
        )

    assert response.status_code == 200
    index_uids = [q["indexUid"] for q in captured["queries"]]
    assert any("Product" in uid for uid in index_uids), index_uids
    assert not any("BlogPost" in uid for uid in index_uids), index_uids
