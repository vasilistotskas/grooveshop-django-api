"""A second factor for platform superusers' admin login.

Only superusers are asked; store admins and staff keep a password. The
sign-in is a few steps held in the session: the password, then a code
(already enrolled) or the enrolment followed by the recovery codes, shown
once, whose confirmation completes the same sign-in. Code validation,
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
from django.contrib.auth import SESSION_KEY
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, RequestFactory
from django.urls import reverse

from admin import staff_mfa
from admin.forms import PlatformAdminAuthenticationForm
from admin.staff_mfa import Step
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]

PASSWORD = "S3cure-passw0rd!"
ERRORNOTE = "Please correct the error"


def _user(**kwargs):
    kwargs.setdefault("is_staff", True)
    user = UserAccountFactory(num_addresses=0, **kwargs)
    user.set_password(PASSWORD)
    user.save()
    return user


def _superuser():
    return _user(is_superuser=True)


# The TOTP step allauth validates against, held for the whole test. With
# the real clock a code computed here and checked by allauth a moment
# later fall in different 30-second steps whenever a boundary passes in
# between (the tolerance is zero steps), and the enrolment fails at random.
_clock = {"counter": 0}


@pytest.fixture(autouse=True)
def _frozen_totp_clock():
    _clock["counter"] = int(time.time()) // 30
    with patch(
        "allauth.mfa.totp.internal.auth.yield_hotp_counters_from_time",
        side_effect=lambda: iter([_clock["counter"]]),
    ):
        yield


def _code(secret: str) -> str:
    """The code for the step allauth currently validates against."""
    return format_hotp_value(hotp_value(secret, _clock["counter"]))


@contextmanager
def _window(offset: int):
    """Move allauth's idea of "now" by *offset* 30-second steps, so a code
    can be used after the one that enrolled (a code is spent once)."""
    start = _clock["counter"]
    _clock["counter"] = start + offset
    try:
        yield
    finally:
        _clock["counter"] = start


def _post(client, **data):
    return client.post(reverse("admin:login"), data)


def _password(client, user, password=PASSWORD):
    return _post(
        client, step="password", username=user.email, password=password
    )


def _answer(client, step, code=""):
    return _post(client, step=step, otp_code=code)


def _reload(client):
    """Open the login page again (it redirects to add ``next``)."""
    return client.get(reverse("admin:login"), follow=True)


def _signed_in(client) -> bool:
    return SESSION_KEY in client.session


def _enrol(user):
    """Walk the enrolment in a client of its own: returns the secret and
    the recovery codes, with *user* now enrolled."""
    client = Client()
    secret = _password(client, user).context["form"].enrolment.secret
    codes = (
        _answer(client, "enrol", _code(secret)).context["form"].recovery_codes
    )
    return secret, list(codes)


class TestWhoIsAsked:
    def test_a_store_admin_logs_in_with_a_password(self):
        user = _user(is_superuser=False)
        client = Client()

        response = _password(client, user)

        assert response.status_code == 302
        assert client.session[SESSION_KEY] == str(user.pk)
        assert staff_mfa.PENDING_SESSION_KEY not in client.session

    def test_a_wrong_password_is_refused_before_any_code_is_asked(self):
        user = _superuser()
        client = Client()

        response = _password(client, user, password="nope")

        form = response.context["form"]
        assert response.status_code == 200
        assert form.step is Step.PASSWORD
        assert form.errors
        assert staff_mfa.PENDING_SESSION_KEY not in client.session
        assert staff_mfa.ENROLMENT_SESSION_KEY not in client.session
        assert not _signed_in(client)

    def test_a_superuser_who_is_not_staff_gets_no_step_at_all(self):
        user = _user(is_staff=False, is_superuser=True)
        client = Client()

        response = _password(client, user)

        assert response.context["form"].step is Step.PASSWORD
        assert staff_mfa.PENDING_SESSION_KEY not in client.session


class TestPasswordThenEnrol:
    def test_the_password_moves_on_without_an_error_or_a_session(self):
        user = _superuser()
        client = Client()

        response = _password(client, user)

        form = response.context["form"]
        body = response.content.decode()
        assert response.status_code == 200
        assert form.step is Step.ENROL
        assert not form.errors
        assert ERRORNOTE not in body
        assert "Password accepted" in body
        assert "Step 2 of 3" in body
        assert form.enrolment.secret in body
        assert "<svg" in body
        assert 'name="username"' not in body
        assert 'name="restart"' in body
        assert not _signed_in(client)
        pending = client.session[staff_mfa.PENDING_SESSION_KEY]
        assert pending["user_id"] == user.pk
        assert pending["step"] == "enrol"

    def test_the_same_secret_is_kept_across_submits_and_reloads(self):
        user = _superuser()
        client = Client()
        first = _password(client, user).context["form"].enrolment.secret

        wrong = _answer(client, "enrol", "000000")
        reloaded = _reload(client)

        assert wrong.context["form"].enrolment.secret == first
        assert reloaded.context["form"].enrolment.secret == first

    def test_a_wrong_code_enrols_nothing_and_stays_on_the_step(self):
        user = _superuser()
        client = Client()
        _password(client, user)

        response = _answer(client, "enrol", "000000")

        form = response.context["form"]
        assert form.step is Step.ENROL
        assert "That code is not correct." in str(form.errors)
        assert not Authenticator.objects.filter(user_id=user.pk).exists()
        assert not _signed_in(client)

    def test_an_empty_code_is_a_field_error_with_the_note(self):
        user = _superuser()
        client = Client()
        _password(client, user)

        response = _answer(client, "enrol", "")

        assert response.context["form"].step is Step.ENROL
        assert ERRORNOTE in response.content.decode()

    def test_the_right_code_shows_the_recovery_codes_once(self):
        user = _superuser()
        client = Client()
        secret = _password(client, user).context["form"].enrolment.secret

        response = _answer(client, "enrol", _code(secret))

        form = response.context["form"]
        body = response.content.decode()
        assert form.step is Step.RECOVERY
        assert not form.errors
        assert ERRORNOTE not in body
        assert "Step 3 of 3" in body
        assert len(form.recovery_codes) == 10
        for recovery_code in form.recovery_codes:
            assert recovery_code in body
        assert "Authenticator set up." in body
        assert 'name="restart"' not in body
        assert Authenticator.objects.filter(
            user_id=user.pk, type=Authenticator.Type.TOTP
        ).exists()
        assert staff_mfa.ENROLMENT_SESSION_KEY not in client.session
        assert not _signed_in(client)

    def test_the_recovery_codes_survive_a_reload_until_confirmed(self):
        user = _superuser()
        client = Client()
        secret = _password(client, user).context["form"].enrolment.secret
        shown = (
            _answer(client, "enrol", _code(secret))
            .context["form"]
            .recovery_codes
        )

        reloaded = _reload(client)

        assert reloaded.context["form"].recovery_codes == shown

    def test_confirming_the_recovery_codes_completes_the_login(self):
        user = _superuser()
        client = Client()
        secret = _password(client, user).context["form"].enrolment.secret
        _answer(client, "enrol", _code(secret))

        response = _answer(client, "recovery")

        assert response.status_code == 302
        assert client.session[SESSION_KEY] == str(user.pk)
        assert staff_mfa.PENDING_SESSION_KEY not in client.session
        assert staff_mfa.ENROLMENT_SESSION_KEY not in client.session
        assert client.get(reverse("admin:index")).status_code == 200

    def test_a_secret_made_for_another_user_is_not_used(self):
        first, other = _superuser(), _superuser()
        client = Client()
        secret = _password(client, first).context["form"].enrolment.secret
        # The session carries the other user's sign-in, but first's secret.
        session = client.session
        session[staff_mfa.PENDING_SESSION_KEY]["user_id"] = other.pk
        session.save()

        response = _answer(client, "enrol", _code(secret))

        assert response.context["form"].errors
        assert not Authenticator.objects.filter(user_id=other.pk).exists()


class TestPasswordThenCode:
    def _enrolled(self):
        user = _superuser()
        secret, codes = _enrol(user)
        return user, secret, codes

    def test_the_password_alone_is_not_enough(self):
        user, _, _ = self._enrolled()
        client = Client()

        response = _password(client, user)

        form = response.context["form"]
        body = response.content.decode()
        assert form.step is Step.CODE
        assert not form.errors
        assert ERRORNOTE not in body
        assert "Step 2 of 2" in body
        assert 'name="otp_code"' in body
        assert 'autocomplete="one-time-code"' in body
        assert 'name="password"' not in body
        assert not _signed_in(client)

    def test_a_correct_code_logs_in(self):
        user, secret, _ = self._enrolled()
        client = Client()
        _password(client, user)

        with _window(1):
            response = _answer(client, "code", _code(secret))

        assert response.status_code == 302
        assert client.session[SESSION_KEY] == str(user.pk)
        assert staff_mfa.PENDING_SESSION_KEY not in client.session

    def test_the_code_that_enrolled_cannot_log_in(self):
        user = _superuser()
        client = Client()
        secret = _password(client, user).context["form"].enrolment.secret
        code = _code(secret)
        _answer(client, "enrol", code)
        other = Client()
        _password(other, user)

        response = _answer(other, "code", code)

        assert response.context["form"].errors
        assert not _signed_in(other)

    def test_a_wrong_code_is_refused_and_the_step_stays(self):
        user, _, _ = self._enrolled()
        client = Client()
        _password(client, user)

        response = _answer(client, "code", "123456")

        form = response.context["form"]
        assert form.step is Step.CODE
        assert "That code is not correct." in str(form.errors)
        assert not _signed_in(client)
        assert client.session[staff_mfa.PENDING_SESSION_KEY]["user_id"] == (
            user.pk
        )

    def test_a_code_cannot_be_used_twice(self):
        user, secret, _ = self._enrolled()
        with _window(-1):
            code = _code(secret)
            first, second = Client(), Client()
            _password(first, user)
            _password(second, user)
            first_response = _answer(first, "code", code)
            second_response = _answer(second, "code", code)

        assert first_response.status_code == 302
        assert second_response.status_code == 200
        assert not _signed_in(second)

    def test_a_recovery_code_works_once(self):
        user, _, codes = self._enrolled()
        first, second = Client(), Client()
        _password(first, user)
        _password(second, user)

        first_response = _answer(first, "code", codes[0])
        second_response = _answer(second, "code", codes[0])

        assert first_response.status_code == 302
        assert second_response.status_code == 200
        assert not _signed_in(second)

    def test_five_wrong_codes_lock_further_tries_even_a_right_one(self):
        user, secret, _ = self._enrolled()
        client = Client()
        _password(client, user)
        for _ in range(staff_mfa.MAX_FAILURES):
            _answer(client, "code", "000000")

        with _window(1):
            response = _answer(client, "code", _code(secret))

        assert "Too many wrong codes" in str(response.context["form"].errors)
        assert not _signed_in(client)

    def test_a_success_clears_the_failures(self):
        user, secret, _ = self._enrolled()
        client = Client()
        _password(client, user)
        for _ in range(staff_mfa.MAX_FAILURES - 1):
            _answer(client, "code", "000000")

        with _window(1):
            _answer(client, "code", _code(secret))

        assert not staff_mfa.is_locked(user)

    def test_the_username_posted_with_a_code_is_ignored(self):
        user, secret, _ = self._enrolled()
        other = _user(is_superuser=False)
        client = Client()
        _password(client, user)

        with _window(1):
            _post(
                client,
                step="code",
                username=other.email,
                password=PASSWORD,
                otp_code=_code(secret),
            )

        assert client.session[SESSION_KEY] == str(user.pk)


class TestThePendingSignIn:
    def test_it_expires(self, settings):
        user = _superuser()
        client = Client()
        _password(client, user)
        settings.STAFF_MFA_PENDING_SIGN_IN_SECONDS = 0

        response = _answer(client, "enrol", "000000")

        form = response.context["form"]
        assert form.step is Step.PASSWORD
        assert "Your sign-in has expired." in str(form.errors)
        assert staff_mfa.PENDING_SESSION_KEY not in client.session
        assert staff_mfa.ENROLMENT_SESSION_KEY not in client.session
        assert 'name="password"' in response.content.decode()

    def test_an_expired_one_is_not_shown_on_a_reload(self, settings):
        user = _superuser()
        client = Client()
        _password(client, user)
        settings.STAFF_MFA_PENDING_SIGN_IN_SECONDS = 0

        response = _reload(client)

        assert response.context["form"].step is Step.PASSWORD
        assert staff_mfa.PENDING_SESSION_KEY not in client.session

    def test_each_step_restarts_the_clock(self, monkeypatch):
        user = _superuser()
        client = Client()
        monkeypatch.setattr(staff_mfa.time, "time", lambda: 1_000_000.0)
        secret = _password(client, user).context["form"].enrolment.secret
        before = client.session[staff_mfa.PENDING_SESSION_KEY]["issued_at"]

        # A minute later, well inside the window: the step must restart it.
        monkeypatch.setattr(staff_mfa.time, "time", lambda: 1_000_060.0)
        _answer(client, "enrol", _code(secret))

        after = client.session[staff_mfa.PENDING_SESSION_KEY]["issued_at"]
        assert (before, after) == (1_000_000.0, 1_000_060.0)
        assert (
            client.session[staff_mfa.PENDING_SESSION_KEY]["step"] == "recovery"
        )

    def test_the_recovery_step_cannot_be_posted_without_one(self):
        client = Client()

        response = _answer(client, "recovery")

        assert response.status_code == 200
        assert "Your sign-in has expired." in str(
            response.context["form"].errors
        )
        assert not _signed_in(client)

    def test_a_finished_sign_in_cannot_be_replayed(self):
        user = _superuser()
        client = Client()
        secret = _password(client, user).context["form"].enrolment.secret
        _answer(client, "enrol", _code(secret))
        _answer(client, "recovery")
        client.logout()

        response = _answer(client, "recovery")

        assert response.status_code == 200
        assert not _signed_in(client)

    def test_start_over_clears_it(self):
        user = _superuser()
        client = Client()
        _password(client, user)

        response = _post(client, step="enrol", restart="1")

        form = response.context["form"]
        assert response.status_code == 200
        assert form.step is Step.PASSWORD
        assert not form.errors
        assert staff_mfa.PENDING_SESSION_KEY not in client.session
        assert staff_mfa.ENROLMENT_SESSION_KEY not in client.session
        assert 'name="username"' in response.content.decode()

    def test_start_over_works_after_it_expired(self, settings):
        user = _superuser()
        client = Client()
        _password(client, user)
        settings.STAFF_MFA_PENDING_SIGN_IN_SECONDS = 0

        response = _post(client, step="code", restart="1")

        assert not response.context["form"].errors

    def test_another_user_signing_in_replaces_it(self):
        first, second = _superuser(), _superuser()
        client = Client()
        _password(client, first)

        _password(client, second)

        pending = client.session[staff_mfa.PENDING_SESSION_KEY]
        assert pending["user_id"] == second.pk

    def test_a_store_admin_signing_in_ends_it(self):
        superuser, store_admin = _superuser(), _user(is_superuser=False)
        client = Client()
        _password(client, superuser)

        response = _password(client, store_admin)

        assert response.status_code == 302
        assert client.session[SESSION_KEY] == str(store_admin.pk)
        assert staff_mfa.PENDING_SESSION_KEY not in client.session
        assert staff_mfa.ENROLMENT_SESSION_KEY not in client.session

    def test_a_wrong_password_for_someone_else_ends_it_too(self):
        first, second = _superuser(), _superuser()
        client = Client()
        _password(client, first)

        _password(client, second, password="nope")

        assert staff_mfa.PENDING_SESSION_KEY not in client.session

    def test_a_post_for_another_step_changes_nothing(self):
        user = _superuser()
        client = Client()
        _password(client, user)

        response = _answer(client, "code", "123456")

        form = response.context["form"]
        assert form.step is Step.ENROL
        assert not form.errors
        assert not staff_mfa.is_locked(user)

    def test_an_account_switched_off_midway_stops_there(self):
        user = _superuser()
        client = Client()
        _password(client, user)
        user.is_active = False
        user.save()

        response = _answer(client, "enrol", "000000")

        assert "Your sign-in has expired." in str(
            response.context["form"].errors
        )
        assert staff_mfa.PENDING_SESSION_KEY not in client.session

    def test_a_form_in_a_plain_session_dict_works_too(self):
        user = _superuser()
        request = RequestFactory().post("/admin/login/")
        request.session = {}

        form = PlatformAdminAuthenticationForm(
            request,
            data={
                "step": "password",
                "username": user.email,
                "password": PASSWORD,
            },
        )

        assert not form.is_valid()
        assert form.get_user() is None
        assert form.step is Step.ENROL
        assert request.session[staff_mfa.PENDING_SESSION_KEY]["user_id"] == (
            user.pk
        )


class TestTheLoginPage:
    def test_a_first_visit_asks_for_the_password_only(self):
        response = Client().get(reverse("admin:login"), follow=True)

        body = response.content.decode()
        assert response.context["form"].step is Step.PASSWORD
        assert 'name="username"' in body
        assert 'name="otp_code"' not in body
        assert "Step " not in body

    def test_a_store_admin_is_not_shown_a_code_field(self):
        user = _user(is_superuser=False)
        client = Client()

        response = _password(client, user)

        assert response.status_code == 302

    def test_the_note_is_for_field_errors_only(self):
        response = _post(Client(), step="password", username="", password="")

        body = response.content.decode()
        assert ERRORNOTE in body

    def test_a_wrong_password_shows_the_error_without_the_note(self):
        user = _superuser()

        response = _password(Client(), user, password="nope")

        body = response.content.decode()
        assert 'role="alert"' in body
        assert ERRORNOTE not in body

    def test_the_recovery_step_offers_copy_and_download(self):
        user = _superuser()
        client = Client()
        secret = _password(client, user).context["form"].enrolment.secret

        body = _answer(client, "enrol", _code(secret)).content.decode()

        assert "js-staff-copy" in body
        assert "js-staff-download" in body
        assert 'id="recovery-codes"' in body
        # Both buttons read the codes list by its id.
        assert body.count('value="recovery-codes"') == 2


class TestReset:
    def test_the_command_removes_the_authenticators(self):
        user = _superuser()
        _enrol(user)
        out = StringIO()

        call_command("reset_staff_mfa", user.email.upper(), stdout=out)

        assert not Authenticator.objects.filter(user_id=user.pk).exists()
        assert "next admin login sets up a new one" in out.getvalue()
        client = Client()
        assert _password(client, user).context["form"].step is Step.ENROL

    def test_it_clears_a_lockout(self):
        user = _superuser()
        _enrol(user)
        client = Client()
        _password(client, user)
        for _ in range(staff_mfa.MAX_FAILURES):
            _answer(client, "code", "000000")
        assert staff_mfa.is_locked(user)

        call_command("reset_staff_mfa", user.email, stdout=StringIO())

        assert not staff_mfa.is_locked(user)

    def test_an_unknown_email_is_an_error(self):
        with pytest.raises(CommandError):
            call_command("reset_staff_mfa", "nobody@example.com")
