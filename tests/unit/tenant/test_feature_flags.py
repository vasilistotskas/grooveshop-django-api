"""Unit tests for IsTenantFeatureEnabled permission classes.

Verifies that blog and loyalty endpoints return 404 when the tenant flag
is False, 200/401 when True, and are never gated on the public schema.

The tests patch ``connection.tenant`` directly — no HTTP middleware
is involved. Django REST Framework evaluates permissions in
``BasePermission.has_permission`` before the view body runs, so we can
exercise the full permission stack via the test client.

URL references use ``reverse()`` so test failures surface as
``NoReverseMatch`` rather than silent wrong-URL 404s.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from tenant.models import Tenant


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tenant(slug: str, **kwargs) -> Tenant:
    """Persist a Tenant row without triggering schema creation."""
    t = Tenant(
        schema_name=slug.replace("-", "_"),
        name=slug,
        slug=slug,
        owner_email=f"owner-{slug}@example.com",
        **kwargs,
    )
    t.auto_create_schema = False
    t.save()
    return t


# ---------------------------------------------------------------------------
# Blog feature flag tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBlogFeatureFlag:
    """``blog_enabled`` flag gates all blog endpoints with 404."""

    def test_list_posts_when_blog_enabled(self, monkeypatch):
        tenant = _make_tenant("ff-blog-on", blog_enabled=True)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-post-list")
        response = client.get(url)
        # 200 OK (no posts exist, but the endpoint is accessible)
        assert response.status_code == status.HTTP_200_OK

    def test_list_posts_when_blog_disabled(self, monkeypatch):
        tenant = _make_tenant("ff-blog-off", blog_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-post-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_categories_when_blog_enabled(self, monkeypatch):
        tenant = _make_tenant("ff-blogcat-on", blog_enabled=True)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-category-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK

    def test_list_categories_when_blog_disabled(self, monkeypatch):
        tenant = _make_tenant("ff-blogcat-off", blog_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-category-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_authors_when_blog_disabled(self, monkeypatch):
        tenant = _make_tenant("ff-blogauth-off", blog_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-author-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_comments_when_blog_disabled(self, monkeypatch):
        tenant = _make_tenant("ff-blogcmt-off", blog_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-comment-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_tags_when_blog_disabled(self, monkeypatch):
        tenant = _make_tenant("ff-blogtag-off", blog_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-tag-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_blog_disabled_is_404_not_403(self, monkeypatch):
        """Disabled feature must look like a missing route, not a
        permission error — 404 hides plan information from callers."""
        tenant = _make_tenant("ff-blog-404", blog_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-post-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        # Must NOT be a 403
        assert response.status_code != status.HTTP_403_FORBIDDEN

    def test_blog_public_schema_never_gated(self, monkeypatch):
        """Public schema (no tenant) must never be gated regardless of
        any hypothetical tenant flag value."""
        monkeypatch.setattr(connection, "tenant", None, raising=False)

        client = APIClient()
        url = reverse("blog-post-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# Loyalty feature flag tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestLoyaltyFeatureFlag:
    """``loyalty_enabled`` flag gates all loyalty endpoints with 404."""

    def test_tiers_when_loyalty_enabled_authenticated(self, monkeypatch):
        """When loyalty is enabled, authenticated users can reach the tiers
        endpoint. The IsAuthenticated check fires after the feature gate, so
        auth is still required even when the feature is on."""
        from user.factories.account import UserAccountFactory

        tenant = _make_tenant("ff-loyal-on", loyalty_enabled=True)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        user = UserAccountFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        url = reverse("loyalty:loyalty-tiers")
        response = client.get(url)
        # 200 OK — feature enabled + authenticated
        assert response.status_code == status.HTTP_200_OK

    def test_tiers_when_loyalty_disabled(self, monkeypatch):
        tenant = _make_tenant("ff-loyal-off", loyalty_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("loyalty:loyalty-tiers")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_summary_when_loyalty_disabled_returns_404(self, monkeypatch):
        """Even authenticated requests get 404 when feature disabled."""
        from user.factories.account import UserAccountFactory

        tenant = _make_tenant("ff-loyal-sum-off", loyalty_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        user = UserAccountFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        url = reverse("loyalty:loyalty-summary")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_summary_requires_auth_when_loyalty_enabled(self, monkeypatch):
        """When loyalty is enabled, unauthenticated requests are
        rejected by ``IsAuthenticated`` — the feature gate must NOT
        bypass auth."""
        tenant = _make_tenant("ff-loyal-auth", loyalty_enabled=True)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("loyalty:loyalty-summary")
        response = client.get(url)
        # 401 (unauthenticated, not 404) — auth check still enforced
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )

    def test_loyalty_disabled_is_404_not_403(self, monkeypatch):
        tenant = _make_tenant("ff-loyal-404", loyalty_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("loyalty:loyalty-tiers")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.status_code != status.HTTP_403_FORBIDDEN

    def test_loyalty_public_schema_never_gated(self, monkeypatch):
        """Public schema bypasses the feature gate entirely.

        On public schema IsLoyaltyEnabled always returns True; IsAuthenticated
        then rejects the anonymous request with 401. The critical assertion is
        that we do NOT get 404 — the feature gate is transparent on the public
        schema.
        """
        monkeypatch.setattr(connection, "tenant", None, raising=False)

        client = APIClient()
        url = reverse("loyalty:loyalty-tiers")
        response = client.get(url)
        # IsLoyaltyEnabled passed → IsAuthenticated rejected anon with 401.
        # Must NOT be 404.
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert response.status_code != status.HTTP_404_NOT_FOUND
