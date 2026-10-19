"""A second factor for platform superusers' admin login.

Only superusers are asked; store admins and staff keep a password. A
superuser without an authenticator gets no session until they enrol one in
the login form itself, and sees the recovery codes once. Code validation,
replay protection and recovery codes are allauth's (``allauth.mfa``).
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from io import StringIO
from unittest.mock import patch

import pytest
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import (
    format_hotp_value,
    hotp_value,
)
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, RequestFactory
from django.urls import reverse

from admin import staff_mfa
from admin.forms import PlatformAdminAuthenticationForm
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

PASSWORD = "S3cure-passw0rd!"


def _user(**kwargs):
    kwargs.setdefault("is_staff", True)
    user = UserAccountFactory(num_addresses=0, **kwargs)
    user.set_password(PASSWORD)
    user.save()
    return user


def _superuser():
    return _user(is_superuser=True)


def _code(secret: str, offset: int = 0) -> str:
    counter = int(time.time()) // 30 + offset
    return format_hotp_value(hotp_value(secret, counter))


@contextmanager
def _window(offset: int):
    """Move allauth's idea of "now" by *offset* 30-second steps, so a code
    can be used after the one that enrolled (a code is spent once; the
    tolerance is zero steps)."""
    counter = int(time.time()) // 30 + offset
    with patch(
        "allauth.mfa.totp.internal.auth.yield_hotp_counters_from_time",
        side_effect=lambda: iter([counter]),
    ):
        yield


def _form(user, otp="", session=None, password=PASSWORD):
    request = RequestFactory().post("/admin/login/")
    request.session = session if session is not None else {}
    form = PlatformAdminAuthenticationForm(
        request,
        data={
            "username": user.email,
            "password": password,
            "otp_code": otp,
        },
    )
    form.is_valid()
    return form, request.session


def _enrol(user):
    """Walk the enrolment: returns the secret and the recovery codes."""
    form, session = _form(user)
    secret = form.enrolment.secret
    done, _ = _form(user, otp=_code(secret), session=session)
    return secret, done.recovery_codes


class TestWhoIsAsked:
    def test_a_store_admin_logs_in_with_a_password(self):
        user = _user(is_superuser=False)

        form, _ = _form(user)

        assert form.is_valid()
        assert form.get_user() == user
        assert form.mfa_stage is None

    def test_a_wrong_password_is_refused_before_any_code_is_asked(self):
        user = _superuser()

        form, session = _form(user, password="nope")

        assert not form.is_valid()
        assert form.mfa_stage is None
        assert staff_mfa.ENROLMENT_SESSION_KEY not in session


class TestFirstLoginEnrols:
    def test_a_superuser_with_no_authenticator_gets_no_session(self):
        user = _superuser()

        form, session = _form(user)

        assert not form.is_valid()
        assert form.mfa_stage == "enrol"
        assert form.get_user() is None
        assert "Set up your authenticator" in str(form.errors)
        assert form.enrolment.secret
        assert form.enrolment_qr.startswith("<svg")
        assert session[staff_mfa.ENROLMENT_SESSION_KEY]["user_id"] == user.pk

    def test_the_same_secret_is_kept_across_submits(self):
        user = _superuser()
        first, session = _form(user)

        second, _ = _form(user, otp="000000", session=session)

        assert second.enrolment.secret == first.enrolment.secret

    def test_a_wrong_code_enrols_nothing(self):
        user = _superuser()
        _, session = _form(user)

        form, _ = _form(user, otp="000000", session=session)

        assert not form.is_valid()
        assert "not correct" in str(form.errors)
        assert not Authenticator.objects.filter(user_id=user.pk).exists()

    def test_the_right_code_enrols_and_shows_recovery_codes_once(self):
        user = _superuser()
        form, session = _form(user)
        secret = form.enrolment.secret

        done, _ = _form(user, otp=_code(secret), session=session)

        assert not done.is_valid()
        assert done.get_user() is None
        assert done.mfa_stage == "recovery"
        assert len(done.recovery_codes) == 10
        assert Authenticator.objects.filter(
            user_id=user.pk, type=Authenticator.Type.TOTP
        ).exists()
        assert staff_mfa.ENROLMENT_SESSION_KEY not in session

    def test_a_secret_made_for_another_user_is_not_used(self):
        first, other = _superuser(), _superuser()
        form, session = _form(first)
        secret = form.enrolment.secret

        done, _ = _form(other, otp=_code(secret), session=session)

        assert not done.is_valid()
        assert not Authenticator.objects.filter(user_id=other.pk).exists()


class TestLoginWithACode:
    def _enrolled(self):
        user = _superuser()
        secret, codes = _enrol(user)
        return user, secret, codes

    def test_the_password_alone_is_not_enough(self):
        user, _, _ = self._enrolled()

        form, _ = _form(user)

        assert not form.is_valid()
        assert form.mfa_stage == "code"
        assert form.get_user() is None

    def test_a_correct_code_logs_in(self):
        user, secret, _ = self._enrolled()

        with _window(1):
            form, _ = _form(user, otp=_code(secret, offset=1))

        assert form.is_valid()
        assert form.get_user() == user

    def test_the_code_that_enrolled_cannot_log_in(self):
        user = _superuser()
        form, session = _form(user)
        secret = form.enrolment.secret
        code = _code(secret)
        _form(user, otp=code, session=session)

        again, _ = _form(user, otp=code)

        assert not again.is_valid()

    def test_a_wrong_code_is_refused(self):
        user, _, _ = self._enrolled()

        form, _ = _form(user, otp="123456")

        assert not form.is_valid()
        assert form.get_user() is None
        assert "not correct" in str(form.errors)

    def test_a_code_cannot_be_used_twice(self):
        user, secret, _ = self._enrolled()
        with _window(-1):
            code = _code(secret, offset=-1)
            first, _ = _form(user, otp=code)
            second, _ = _form(user, otp=code)

        assert first.is_valid()
        assert not second.is_valid()

    def test_a_recovery_code_works_once(self):
        user, _, codes = self._enrolled()

        first, _ = _form(user, otp=codes[0])
        second, _ = _form(user, otp=codes[0])

        assert first.is_valid()
        assert not second.is_valid()

    def test_five_wrong_codes_lock_further_tries_even_a_right_one(self):
        user, secret, _ = self._enrolled()
        for _ in range(staff_mfa.MAX_FAILURES):
            _form(user, otp="000000")

        with _window(1):
            form, _ = _form(user, otp=_code(secret, offset=1))

        assert not form.is_valid()
        assert "Too many wrong codes" in str(form.errors)

    def test_a_success_clears_the_failures(self):
        user, secret, _ = self._enrolled()
        for _ in range(staff_mfa.MAX_FAILURES - 1):
            _form(user, otp="000000")
        with _window(1):
            _form(user, otp=_code(secret, offset=1))

        assert not staff_mfa.is_locked(user)


class TestTheLoginPage:
    def _post(self, client, user, otp=""):
        return client.post(
            reverse("admin:login"),
            {"username": user.email, "password": PASSWORD, "otp_code": otp},
        )

    def test_first_login_shows_the_secret_and_gives_no_session(self):
        user = _superuser()
        client = Client()

        response = self._post(client, user)

        assert response.status_code == 200
        assert response.context["form"].mfa_stage == "enrol"
        assert response.context["form"].enrolment.secret in (
            response.content.decode()
        )
        assert "<svg" in response.content.decode()
        assert "_auth_user_id" not in client.session

    def test_recovery_codes_are_shown_after_enrolling(self):
        user = _superuser()
        client = Client()
        secret = self._post(client, user).context["form"].enrolment.secret

        response = self._post(client, user, _code(secret))

        body = response.content.decode()
        assert "Recovery codes" in body
        for code in response.context["form"].recovery_codes:
            assert code in body
        assert "_auth_user_id" not in client.session

    def test_the_code_field_is_asked_for_once_enrolled(self):
        user = _superuser()
        _enrol(user)

        response = self._post(Client(), user)

        assert 'name="otp_code"' in response.content.decode()

    def test_a_store_admin_is_not_shown_a_code_field(self):
        user = _user(is_superuser=False)

        response = self._post(Client(), user)

        assert 'name="otp_code"' not in response.content.decode()


class TestReset:
    def test_the_command_removes_the_authenticators(self):
        user = _superuser()
        _enrol(user)
        out = StringIO()

        call_command("reset_staff_mfa", user.email.upper(), stdout=out)

        assert not Authenticator.objects.filter(user_id=user.pk).exists()
        assert "next admin login sets up a new one" in out.getvalue()
        form, _ = _form(user)
        assert form.mfa_stage == "enrol"

    def test_it_clears_a_lockout(self):
        user = _superuser()
        _enrol(user)
        for _ in range(staff_mfa.MAX_FAILURES):
            _form(user, otp="000000")
        assert staff_mfa.is_locked(user)

        call_command("reset_staff_mfa", user.email, stdout=StringIO())

        assert not staff_mfa.is_locked(user)

    def test_an_unknown_email_is_an_error(self):
        with pytest.raises(CommandError):
            call_command("reset_staff_mfa", "nobody@example.com")
