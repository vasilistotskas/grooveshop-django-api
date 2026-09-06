"""Tests for tenant.auth_backends.PlatformStaffBackend."""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from tenant.auth_backends import (
    PLATFORM_STAFF_BACKEND_PATH,
    PlatformStaffBackend,
    is_platform_staff_session,
)
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


class TestAuthenticateIsInert:
    """``authenticate()`` must never succeed — see module docstring for
    why (keeps this backend inert for the storefront's global
    ``django.contrib.auth.authenticate()`` dispatch chain)."""

    def test_always_returns_none_for_valid_staff_credentials(self):
        staff = UserAccountFactory(is_staff=True, plain_password="pw12345")
        backend = PlatformStaffBackend()
        request = RequestFactory().get("/")
        assert (
            backend.authenticate(
                request, username=staff.email, password="pw12345"
            )
            is None
        )


class TestAuthenticateStaff:
    def test_valid_staff_credentials_return_user(self):
        staff = UserAccountFactory(is_staff=True, plain_password="pw12345")
        backend = PlatformStaffBackend()
        request = RequestFactory().get("/")
        user = backend.authenticate_staff(
            request, username=staff.email, password="pw12345"
        )
        assert user is not None
        assert user.pk == staff.pk

    def test_non_staff_user_rejected(self):
        shopper = UserAccountFactory(is_staff=False, plain_password="pw12345")
        backend = PlatformStaffBackend()
        request = RequestFactory().get("/")
        assert (
            backend.authenticate_staff(
                request, username=shopper.email, password="pw12345"
            )
            is None
        )

    def test_inactive_staff_rejected(self):
        staff = UserAccountFactory(
            is_staff=True, is_active=False, plain_password="pw12345"
        )
        backend = PlatformStaffBackend()
        request = RequestFactory().get("/")
        assert (
            backend.authenticate_staff(
                request, username=staff.email, password="pw12345"
            )
            is None
        )

    def test_wrong_password_rejected(self):
        staff = UserAccountFactory(is_staff=True, plain_password="pw12345")
        backend = PlatformStaffBackend()
        request = RequestFactory().get("/")
        assert (
            backend.authenticate_staff(
                request, username=staff.email, password="wrong-password"
            )
            is None
        )

    def test_unknown_email_rejected(self):
        backend = PlatformStaffBackend()
        request = RequestFactory().get("/")
        assert (
            backend.authenticate_staff(
                request,
                username="nobody-platform@example.com",
                password="pw12345",
            )
            is None
        )


class TestGetUser:
    def test_returns_matching_user(self):
        staff = UserAccountFactory(is_staff=True)
        backend = PlatformStaffBackend()
        user = backend.get_user(staff.pk)
        assert user is not None
        assert user.pk == staff.pk

    def test_missing_user_returns_none(self):
        backend = PlatformStaffBackend()
        assert backend.get_user(999_999_999) is None

    def test_revoked_staff_no_longer_restores(self):
        """Unticking is_staff must end the SESSION, not just the login.

        The stamp this method applies is the only thing `is_store_staff`
        and `TenantRolePermissionBackend` look at, so a restored session
        for a revoked account kept full store-staff API access for the
        cookie's remaining life — while the admin UI, which reads the
        flag directly, correctly locked them out and made the revocation
        look like it had worked.
        """
        revoked = UserAccountFactory(is_staff=False)
        backend = PlatformStaffBackend()
        assert backend.get_user(revoked.pk) is None

    def test_inactive_staff_no_longer_restores(self):
        inactive = UserAccountFactory(is_staff=True, is_active=False)
        backend = PlatformStaffBackend()
        assert backend.get_user(inactive.pk) is None


class TestIsPlatformStaffSession:
    def test_true_when_backend_matches(self):
        request = RequestFactory().get("/")
        request.session = {"_auth_user_backend": PLATFORM_STAFF_BACKEND_PATH}
        assert is_platform_staff_session(request) is True

    def test_false_when_backend_differs(self):
        request = RequestFactory().get("/")
        request.session = {
            "_auth_user_backend": "django.contrib.auth.backends.ModelBackend"
        }
        assert is_platform_staff_session(request) is False

    def test_false_when_session_missing_backend_key(self):
        request = RequestFactory().get("/")
        request.session = {}
        assert is_platform_staff_session(request) is False

    def test_false_when_no_session_attribute(self):
        request = RequestFactory().get("/")
        assert is_platform_staff_session(request) is False
