"""Wrong passwords are counted at every staff sign-in.

allauth's limiter covers ``/_allauth/`` only, so the Django admin login and
the staff token endpoint were open to unlimited guessing; a store Owner or
Admin signs in with a password alone. The count lives in
``tenant.staff_login_limits`` and both entry points reach it through
``PlatformStaffBackend.authenticate_staff``.
"""

from __future__ import annotations

import pytest
from django.test import Client
from django.urls import reverse
from django_tenants.utils import schema_context
from rest_framework.test import APIClient

from tenant.auth_backends import PlatformStaffBackend
from user.factories.account import UserAccountFactory

PASSWORD = "S3cure-passw0rd!"
TOKEN_LOGIN = "/api/v1/platform/auth/login"
LIMITS = {
    "MAX_FAILURES_PER_ACCOUNT": 3,
    "MAX_FAILURES_PER_IP": 5,
    "WINDOW_SECONDS": 900,
}

pytestmark = [
    pytest.mark.urls("tenant.urls_public"),
    pytest.mark.django_db,
    pytest.mark.assert_english,
]


@pytest.fixture(autouse=True)
def _limits(settings):
    settings.STAFF_LOGIN_LIMITS = LIMITS


def _staff(**kwargs):
    user = UserAccountFactory(num_addresses=0, is_staff=True, **kwargs)
    user.set_password(PASSWORD)
    user.save()
    return user


def _admin_login(email, password, ip="10.0.0.1"):
    return Client(REMOTE_ADDR=ip).post(
        reverse("admin:login"),
        {"step": "password", "username": email, "password": password},
    )


def _token_login(email, password, ip="10.0.0.1"):
    return APIClient(REMOTE_ADDR=ip).post(
        TOKEN_LOGIN, {"email": email, "password": password}
    )


def _admin_signed_in(response) -> bool:
    return response.status_code == 302


class TestAdminLogin:
    def test_the_right_password_is_refused_once_the_account_is_locked(self):
        user = _staff()
        for _attempt in range(LIMITS["MAX_FAILURES_PER_ACCOUNT"]):
            assert not _admin_signed_in(_admin_login(user.email, "wrong"))

        response = _admin_login(user.email, PASSWORD)

        assert not _admin_signed_in(response)
        assert response.context["form"].errors

    def test_a_locked_unknown_account_answers_like_a_known_one(self):
        known = _staff()
        for _attempt in range(LIMITS["MAX_FAILURES_PER_ACCOUNT"]):
            _admin_login(known.email, "wrong")
            _admin_login("nobody@example.com", "wrong")

        known_errors = (
            _admin_login(known.email, PASSWORD).context["form"].errors
        )
        unknown_errors = (
            _admin_login("nobody@example.com", PASSWORD).context["form"].errors
        )

        assert known_errors.as_data() == unknown_errors.as_data()

    def test_a_success_clears_the_account_count(self):
        user = _staff()
        for _attempt in range(LIMITS["MAX_FAILURES_PER_ACCOUNT"] - 1):
            _admin_login(user.email, "wrong")
        assert _admin_signed_in(_admin_login(user.email, PASSWORD))

        for _attempt in range(LIMITS["MAX_FAILURES_PER_ACCOUNT"] - 1):
            _admin_login(user.email, "wrong")

        assert _admin_signed_in(_admin_login(user.email, PASSWORD))


class TestTokenLogin:
    def test_the_right_password_is_refused_once_the_account_is_locked(self):
        user = _staff()
        for _attempt in range(LIMITS["MAX_FAILURES_PER_ACCOUNT"]):
            assert _token_login(user.email, "wrong").status_code == 401

        response = _token_login(user.email, PASSWORD)

        assert response.status_code == 429
        assert response.data["code"] == "login_locked"

    def test_the_two_entry_points_share_one_count(self):
        user = _staff()
        for _attempt in range(LIMITS["MAX_FAILURES_PER_ACCOUNT"] - 1):
            _admin_login(user.email, "wrong")
        _token_login(user.email, "wrong")

        assert not _admin_signed_in(_admin_login(user.email, PASSWORD))


class TestAddressLimit:
    def test_one_address_failing_across_accounts_is_stopped(self):
        accounts = [_staff() for _ in range(LIMITS["MAX_FAILURES_PER_IP"])]
        for user in accounts:
            _admin_login(user.email, "wrong", ip="10.9.9.9")
        fresh = _staff()

        response = _admin_login(fresh.email, PASSWORD, ip="10.9.9.9")

        assert not _admin_signed_in(response)

    def test_another_address_is_not_affected_by_it(self):
        for user in [_staff() for _ in range(LIMITS["MAX_FAILURES_PER_IP"])]:
            _admin_login(user.email, "wrong", ip="10.9.9.9")
        fresh = _staff()

        assert _admin_signed_in(
            _admin_login(fresh.email, PASSWORD, ip="10.8.8.8")
        )

    def test_a_locked_account_does_not_lock_the_other_account_of_an_address(
        self,
    ):
        victim, other = _staff(), _staff()
        for _attempt in range(LIMITS["MAX_FAILURES_PER_ACCOUNT"]):
            _admin_login(victim.email, "wrong", ip="10.7.7.7")

        assert _admin_signed_in(
            _admin_login(other.email, PASSWORD, ip="10.7.7.7")
        )

    def test_a_success_does_not_clear_the_address_count(self):
        """Otherwise one valid login would reset a guessing run."""
        own = _staff()
        for index in range(LIMITS["MAX_FAILURES_PER_IP"] - 1):
            _admin_login(f"nobody{index}@example.com", "wrong", ip="10.6.6.6")
        assert _admin_signed_in(_admin_login(own.email, PASSWORD, "10.6.6.6"))
        _admin_login("someone@example.com", "wrong", ip="10.6.6.6")

        assert not _admin_signed_in(
            _admin_login(_staff().email, PASSWORD, ip="10.6.6.6")
        )


class TestBackend:
    def test_lock_raises_even_for_the_right_password(self):
        from tenant.staff_login_limits import StaffLoginLocked

        user = _staff()
        backend = PlatformStaffBackend()
        for _attempt in range(LIMITS["MAX_FAILURES_PER_ACCOUNT"]):
            assert backend.authenticate_staff(None, user.email, "wrong") is None

        with pytest.raises(StaffLoginLocked):
            backend.authenticate_staff(None, user.email, PASSWORD)

    def test_the_count_is_shared_by_every_store_host(self):
        """Staff are public identities: a count kept per schema would give
        every store host its own allowance."""
        user = _staff()
        backend = PlatformStaffBackend()
        for index in range(LIMITS["MAX_FAILURES_PER_ACCOUNT"]):
            schema = "public" if index % 2 else "some_store"
            with schema_context(schema):
                backend.authenticate_staff(None, user.email, "wrong")

        from tenant.staff_login_limits import StaffLoginLocked

        with schema_context("another_store"), pytest.raises(StaffLoginLocked):
            backend.authenticate_staff(None, user.email, PASSWORD)


class TestUnlocking:
    def _lock(self, user):
        for _attempt in range(LIMITS["MAX_FAILURES_PER_ACCOUNT"]):
            _admin_login(user.email, "wrong")
        assert not _admin_signed_in(_admin_login(user.email, PASSWORD))

    def test_a_password_reset_clears_the_account_count(self):
        from admin.password_reset import StaffSetPasswordForm

        user = _staff()
        self._lock(user)
        new_password = "An0ther-passw0rd!"
        form = StaffSetPasswordForm(
            user,
            {"new_password1": new_password, "new_password2": new_password},
        )
        assert form.is_valid(), form.errors
        form.save()

        assert _admin_signed_in(_admin_login(user.email, new_password))

    def test_reset_staff_mfa_clears_the_account_count(self):
        from io import StringIO

        from django.core.management import call_command

        user = _staff()
        self._lock(user)

        call_command("reset_staff_mfa", user.email, stdout=StringIO())

        assert _admin_signed_in(_admin_login(user.email, PASSWORD))
