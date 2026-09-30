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

from tests.utils.staff import store_tenant

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _loyalty_runtime(enabled: bool):
    """Set the merchant tier of the loyalty gate.

    ``LOYALTY_ENABLED`` ships False and these tests create no Setting
    rows, so without this every loyalty endpoint would 404 on the
    runtime gate before the flag under test was ever consulted.
    """
    from unittest.mock import patch

    return patch(
        "extra_settings.models.Setting.get",
        side_effect=lambda key, default=None: (
            enabled if key == "LOYALTY_ENABLED" else default
        ),
    )


# ---------------------------------------------------------------------------
# Blog feature flag tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBlogFeatureFlag:
    """``blog_enabled`` flag gates all blog endpoints with 404."""

    def test_list_posts_when_blog_enabled(self, monkeypatch):
        tenant = store_tenant("ff_blog_on", blog_enabled=True)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-post-list")
        response = client.get(url)
        # 200 OK (no posts exist, but the endpoint is accessible)
        assert response.status_code == status.HTTP_200_OK

    def test_list_posts_when_blog_disabled(self, monkeypatch):
        tenant = store_tenant("ff_blog_off", blog_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-post-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_categories_when_blog_enabled(self, monkeypatch):
        tenant = store_tenant("ff_blogcat_on", blog_enabled=True)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-category-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_200_OK

    def test_list_categories_when_blog_disabled(self, monkeypatch):
        tenant = store_tenant("ff_blogcat_off", blog_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-category-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_authors_when_blog_disabled(self, monkeypatch):
        tenant = store_tenant("ff_blogauth_off", blog_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-author-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_comments_when_blog_disabled(self, monkeypatch):
        tenant = store_tenant("ff_blogcmt_off", blog_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-comment-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_list_tags_when_blog_disabled(self, monkeypatch):
        tenant = store_tenant("ff_blogtag_off", blog_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("blog-tag-list")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_blog_disabled_is_404_not_403(self, monkeypatch):
        """Disabled feature must look like a missing route, not a
        permission error — 404 hides plan information from callers."""
        tenant = store_tenant("ff_blog_404", blog_enabled=False)
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
        """When loyalty is enabled, the tier ladder is readable."""
        from user.factories.account import UserAccountFactory

        tenant = store_tenant("ff_loyal_on", loyalty_enabled=True)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        user = UserAccountFactory()
        client = APIClient()
        client.force_authenticate(user=user)
        url = reverse("loyalty:loyalty-tiers")
        with _loyalty_runtime(True):
            response = client.get(url)
        # 200 OK — feature enabled + authenticated
        assert response.status_code == status.HTTP_200_OK

    def test_tiers_are_readable_anonymously(self, monkeypatch):
        """The tier ladder is the /loyalty-program page's whole content.

        It is marketing copy — the same for every visitor of a store —
        so it must render for the people the page is aimed at, who are
        by definition not signed in. Everything that touches a
        CUSTOMER's points stays authenticated (see the summary tests
        below).
        """
        tenant = store_tenant("ff_loyal_anon", loyalty_enabled=True)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        with _loyalty_runtime(True):
            response = client.get(reverse("loyalty:loyalty-tiers"))

        assert response.status_code == status.HTTP_200_OK

    def test_tiers_404_when_the_merchant_switches_loyalty_off(
        self, monkeypatch
    ):
        """The runtime tier of the gate, on a public and indexable page.

        A store that turned the programme off must stop advertising its
        tiers, not merely hide the account widgets. Fails CLOSED, like
        every other commercial gate: ``LOYALTY_ENABLED`` ships False, so
        an absent row is "off" rather than "on".
        """
        tenant = store_tenant("ff_loyal_runtime_off", loyalty_enabled=True)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        with _loyalty_runtime(False):
            response = client.get(reverse("loyalty:loyalty-tiers"))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_tiers_when_loyalty_disabled(self, monkeypatch):
        tenant = store_tenant("ff_loyal_off", loyalty_enabled=False)
        monkeypatch.setattr(connection, "tenant", tenant, raising=False)

        client = APIClient()
        url = reverse("loyalty:loyalty-tiers")
        response = client.get(url)
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_summary_when_loyalty_disabled_returns_404(self, monkeypatch):
        """Even authenticated requests get 404 when feature disabled."""
        from user.factories.account import UserAccountFactory

        tenant = store_tenant("ff_loyal_sum_off", loyalty_enabled=False)
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
        tenant = store_tenant("ff_loyal_auth", loyalty_enabled=True)
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
        tenant = store_tenant("ff_loyal_404", loyalty_enabled=False)
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

        Asserted on ``summary`` rather than ``tiers``: the tier ladder is
        deliberately anonymous now, so it could not tell a bypassed gate
        from a passed one.
        """
        monkeypatch.setattr(connection, "tenant", None, raising=False)

        client = APIClient()
        url = reverse("loyalty:loyalty-summary")
        response = client.get(url)
        # IsLoyaltyEnabled passed → IsAuthenticated rejected anon with 401.
        # Must NOT be 404.
        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert response.status_code != status.HTTP_404_NOT_FOUND
