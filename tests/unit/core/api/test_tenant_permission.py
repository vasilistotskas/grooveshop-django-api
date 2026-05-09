"""Unit tests for IsTenantMemberOrReadOnly and HasTenantAccess (FIX 3).

Tests the new global default permission class behaviour.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from tenant.membership import HasTenantAccess, IsTenantMemberOrReadOnly


def _make_request(method="GET", is_authenticated=False, user=None):
    req = MagicMock()
    req.method = method
    if user is None:
        user = MagicMock()
        user.is_authenticated = is_authenticated
    req.user = user
    return req


class TestIsTenantMemberOrReadOnly:
    """Permission behaves like IsAuthenticatedOrReadOnly but also validates
    tenant membership for authenticated requests."""

    def test_anonymous_get_allowed(self):
        perm = IsTenantMemberOrReadOnly()
        request = _make_request("GET", is_authenticated=False)
        assert perm.has_permission(request, None) is True

    def test_anonymous_head_allowed(self):
        perm = IsTenantMemberOrReadOnly()
        request = _make_request("HEAD", is_authenticated=False)
        assert perm.has_permission(request, None) is True

    def test_anonymous_options_allowed(self):
        perm = IsTenantMemberOrReadOnly()
        request = _make_request("OPTIONS", is_authenticated=False)
        assert perm.has_permission(request, None) is True

    def test_anonymous_post_denied(self):
        perm = IsTenantMemberOrReadOnly()
        request = _make_request("POST", is_authenticated=False)
        assert perm.has_permission(request, None) is False

    def test_anonymous_put_denied(self):
        perm = IsTenantMemberOrReadOnly()
        request = _make_request("PUT", is_authenticated=False)
        assert perm.has_permission(request, None) is False

    def test_authenticated_post_no_tenant_allowed(self):
        """In public schema, tenant check is skipped — authenticated write
        allowed (admin/platform paths)."""
        perm = IsTenantMemberOrReadOnly()
        request = _make_request("POST", is_authenticated=True)
        with patch("tenant.membership.get_current_tenant", return_value=None):
            assert perm.has_permission(request, None) is True

    def test_authenticated_post_with_tenant_membership(self):
        """Authenticated user with membership can write on tenant schema."""
        perm = IsTenantMemberOrReadOnly()
        request = _make_request("POST", is_authenticated=True)
        mock_tenant = MagicMock()
        with patch(
            "tenant.membership.get_current_tenant", return_value=mock_tenant
        ):
            with patch(
                "tenant.membership.user_has_tenant_access", return_value=True
            ):
                assert perm.has_permission(request, None) is True

    def test_authenticated_post_without_tenant_membership(self):
        """Authenticated user without membership cannot write on tenant schema."""
        perm = IsTenantMemberOrReadOnly()
        request = _make_request("POST", is_authenticated=True)
        mock_tenant = MagicMock()
        with patch(
            "tenant.membership.get_current_tenant", return_value=mock_tenant
        ):
            with patch(
                "tenant.membership.user_has_tenant_access", return_value=False
            ):
                assert perm.has_permission(request, None) is False

    def test_safe_methods_bypass_tenant_check(self):
        """GET by a user without tenant membership still passes (public catalog)."""
        perm = IsTenantMemberOrReadOnly()
        request = _make_request("GET", is_authenticated=True)
        # Even if get_current_tenant returns a tenant and user has no membership:
        mock_tenant = MagicMock()
        with patch(
            "tenant.membership.get_current_tenant", return_value=mock_tenant
        ):
            with patch(
                "tenant.membership.user_has_tenant_access", return_value=False
            ):
                assert perm.has_permission(request, None) is True


class TestHastenantAccess:
    def test_unauthenticated_user_denied(self):
        perm = HasTenantAccess()
        user = MagicMock()
        user.is_authenticated = False
        request = MagicMock()
        request.user = user
        with patch("tenant.membership.get_current_tenant", return_value=None):
            assert perm.has_permission(request, None) is False

    def test_authenticated_user_with_membership_allowed(self):
        perm = HasTenantAccess()
        user = MagicMock()
        user.is_authenticated = True
        request = MagicMock()
        request.user = user
        with patch(
            "tenant.membership.user_has_tenant_access", return_value=True
        ):
            assert perm.has_permission(request, None) is True
