"""Unit tests for the staff-only ``HasTenantAccess`` permission.

``UserTenantMembership`` grants a PLATFORM-PUBLIC identity operator
access over a tenant. It is not how customers are scoped to a store —
shoppers live in their tenant's own schema, so being authenticated on
this host already means "customer of this store". The former
``IsTenantMemberOrReadOnly`` global default additionally demanded a
membership from every authenticated writer, which no shopper can hold
(public-schema table, FK to the public user table), so it refused every
customer write. It is gone; DRF's ``IsAuthenticatedOrReadOnly`` is the
default now, and this module covers what remains.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tenant.membership import HasTenantAccess


class TestHasTenantAccess:
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

    def test_authenticated_user_without_membership_denied(self):
        """A staff surface must stay closed to a shopper who happens to
        be authenticated on this host."""
        perm = HasTenantAccess()
        user = MagicMock()
        user.is_authenticated = True
        request = MagicMock()
        request.user = user
        with patch(
            "tenant.membership.user_has_tenant_access", return_value=False
        ):
            assert perm.has_permission(request, None) is False


class TestGlobalDefaultIsNotMembershipGated:
    """Regression: the DRF default must not require a membership.

    Requiring one made every authenticated customer write a 403, and the
    signup-time grant that tried to satisfy it raised ForeignKeyViolation
    — signup 500'd with the account already written.
    """

    def test_default_permission_is_drf_builtin(self):
        from django.conf import settings

        assert settings.REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] == [
            "rest_framework.permissions.IsAuthenticatedOrReadOnly"
        ]

    def test_membership_permission_class_is_gone(self):
        import tenant.membership as membership

        assert not hasattr(membership, "IsTenantMemberOrReadOnly")
