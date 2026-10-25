"""Blog search must respect the tenant's blog plan flag.

Every blog VIEWSET chains `IsBlogEnabled`, which 404s when the plan flag
is off. The blog search endpoint, which serves the same content, did not —
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
