"""The staff token endpoint asks the same second factor as the admin login.

A platform superuser is store staff in every store, so a token minted from a
password alone would reach every store. ``admin.staff_mfa`` decides who is
asked (``requires_mfa``), checks the code, and counts the wrong ones; the
endpoint goes through those helpers, so the token and the admin form cannot
drift apart.
"""

from __future__ import annotations

import pytest
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal.auth import (
    TOTP,
    generate_totp_secret,
)
from django.core.cache import cache
from django_tenants.utils import get_public_schema_name, schema_context
from rest_framework.test import APIClient

from admin import staff_mfa
from tenant.models import PlatformStaffToken
from tests.utils.totp import frozen_totp_clock, totp_code
from user.factories.account import UserAccountFactory

LOGIN = "/api/v1/platform/auth/login"
PASSWORD = "staff-mfa-pass-123"

pytestmark = [
    pytest.mark.urls("tenant.urls_public"),
    pytest.mark.django_db,
]


@pytest.fixture(autouse=True)
def _held_totp_step():
    with frozen_totp_clock():
        yield


def _superuser(**kwargs):
    user = UserAccountFactory(
        num_addresses=0, is_staff=True, is_superuser=True, **kwargs
    )
    user.set_password(PASSWORD)
    user.save()
    return user


def _enrol(user) -> tuple[str, list[str]]:
    secret = generate_totp_secret()
    with schema_context(get_public_schema_name()):
        TOTP.activate(user, secret)
        codes = RecoveryCodes.activate(user).generate_codes()
    return secret, codes


def _login(**data):
    return APIClient().post(LOGIN, data)


def _tokens(user) -> int:
    with schema_context(get_public_schema_name()):
        return PlatformStaffToken.objects.filter(user=user).count()


class TestSuperuserNeedsASecondFactor:
    def test_password_alone_mints_nothing_for_an_enrolled_superuser(self):
        user = _superuser()
        _enrol(user)

        response = _login(email=user.email, password=PASSWORD)

        assert response.status_code == 401, response.data
        assert response.data["code"] == "mfa_code_required"
        assert _tokens(user) == 0

    def test_a_superuser_without_an_authenticator_is_sent_to_enrol(self):
        user = _superuser()

        response = _login(email=user.email, password=PASSWORD)

        assert response.status_code == 403, response.data
        assert response.data["code"] == "mfa_enrolment_required"
        assert _tokens(user) == 0

    def test_a_valid_authenticator_code_mints_a_token(self):
        user = _superuser()
        secret, _ = _enrol(user)

        response = _login(
            email=user.email, password=PASSWORD, code=totp_code(secret)
        )

        assert response.status_code == 200, response.data
        assert response.data["token"]
        assert _tokens(user) == 1

    def test_a_recovery_code_mints_a_token_and_is_spent(self):
        user = _superuser()
        _, codes = _enrol(user)

        first = _login(email=user.email, password=PASSWORD, code=codes[0])
        again = _login(email=user.email, password=PASSWORD, code=codes[0])

        assert first.status_code == 200, first.data
        assert again.status_code == 401
        assert again.data["code"] == "mfa_wrong_code"

    def test_a_wrong_code_is_refused_and_counted(self):
        user = _superuser()
        _enrol(user)

        response = _login(email=user.email, password=PASSWORD, code="000000")

        assert response.status_code == 401
        assert response.data["code"] == "mfa_wrong_code"
        assert cache.get(staff_mfa._failures_key(user)) == 1
        assert _tokens(user) == 0

    def test_the_lockout_is_the_admin_forms_and_refuses_a_right_code(self):
        user = _superuser()
        secret, _ = _enrol(user)
        for _attempt in range(staff_mfa.MAX_FAILURES):
            _login(email=user.email, password=PASSWORD, code="000000")

        response = _login(
            email=user.email, password=PASSWORD, code=totp_code(secret)
        )

        assert response.status_code == 429
        assert response.data["code"] == "mfa_locked"
        assert _tokens(user) == 0

    def test_failures_made_here_lock_the_admin_form_too(self):
        user = _superuser()
        _enrol(user)
        for _attempt in range(staff_mfa.MAX_FAILURES):
            _login(email=user.email, password=PASSWORD, code="000000")

        failures = cache.get(staff_mfa._failures_key(user), 0)
        assert failures >= staff_mfa.MAX_FAILURES

    def test_a_right_code_clears_the_failures(self):
        user = _superuser()
        secret, _ = _enrol(user)
        _login(email=user.email, password=PASSWORD, code="000000")

        response = _login(
            email=user.email, password=PASSWORD, code=totp_code(secret)
        )

        assert response.status_code == 200, response.data
        assert not cache.get(staff_mfa._failures_key(user))

    def test_store_staff_who_is_not_a_superuser_stays_password_only(self):
        from tenant.enum import TenantMembershipRole
        from tenant.models import UserTenantMembership
        from tests.utils.staff import store_tenant

        user = UserAccountFactory(
            num_addresses=0, is_staff=True, is_superuser=False
        )
        user.set_password(PASSWORD)
        user.save()
        UserTenantMembership.objects.create(
            user=user,
            tenant=store_tenant("second_factor_store"),
            role=TenantMembershipRole.OWNER,
            is_active=True,
        )

        response = _login(email=user.email, password=PASSWORD)

        assert response.status_code == 200, response.data
