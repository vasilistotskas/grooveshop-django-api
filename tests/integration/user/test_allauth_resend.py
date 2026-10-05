"""Resend of the login code and the email verification code.

Both are allauth's own headless endpoints, switched on with
``ACCOUNT_*_SUPPORTS_RESEND`` / ``..._SUPPORTS_CHANGE`` — the storefront
only proxies them. These tests drive the real endpoints so a settings
regression (the flag off, the cooldown dropped) fails here and not as a
silent "no code arrived" in production.
"""

from __future__ import annotations

import json
import re

import pytest
from django.core import mail
from django.core.cache import cache
from django.http import HttpResponse
from django.test import Client, RequestFactory
from rest_framework import status

from user.factories.account import UserAccountFactory

BASE = "/_allauth/app/v1"
PASSWORD = "S3cure-pass-phrase!"


def _post(client, path, body=None, token=None):
    headers = {"HTTP_X_SESSION_TOKEN": token} if token else {}
    return client.post(
        f"{BASE}{path}",
        data=json.dumps(body or {}),
        content_type="application/json",
        **headers,
    )


def _code_from_last_mail() -> str:
    match = re.search(r"\b([A-Z0-9]{6})\b", mail.outbox[-1].body)
    assert match, mail.outbox[-1].body
    return match.group(1)


@pytest.fixture
def client():
    return Client()


@pytest.mark.django_db
class TestLoginCodeResend:
    def _request_code(self, client, user):
        response = _post(client, "/auth/code/request", {"email": user.email})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
        return response.json()["meta"]["session_token"]

    def test_resend_sends_a_fresh_code(self, client):
        user = UserAccountFactory()
        token = self._request_code(client, user)
        first = _code_from_last_mail()
        sent = len(mail.outbox)

        response = _post(client, "/auth/code/resend", token=token)

        assert response.status_code == status.HTTP_200_OK
        assert len(mail.outbox) == sent + 1
        assert mail.outbox[-1].to == [user.email]
        assert _code_from_last_mail() != first

    def test_resend_is_capped_per_pending_flow(self, client, settings):
        user = UserAccountFactory()
        token = self._request_code(client, user)
        quota = settings.ACCOUNT_LOGIN_BY_CODE_SUPPORTS_RESEND

        for _ in range(quota):
            response = _post(client, "/auth/code/resend", token=token)
            assert response.status_code == status.HTTP_200_OK

        response = _post(client, "/auth/code/resend", token=token)
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_resend_without_a_pending_code_is_a_conflict(self, client):
        response = _post(client, "/auth/code/resend")
        assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.django_db
class TestEmailVerificationResend:
    def _signup(self, client, email="new.shopper@example.com"):
        response = _post(
            client,
            "/auth/signup",
            {"email": email, "password": PASSWORD},
        )
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, (
            response.content
        )
        pending = [
            flow
            for flow in response.json()["data"]["flows"]
            if flow["id"] == "verify_email"
        ]
        assert pending and pending[0]["is_pending"]
        return response.json()["meta"]["session_token"]

    @staticmethod
    def _end_cooldown():
        cache.clear()

    def test_resend_inside_the_cooldown_is_a_429(self, client):
        token = self._signup(client)

        response = _post(client, "/auth/email/verify/resend", token=token)

        assert response.status_code == status.HTTP_429_TOO_MANY_REQUESTS

    def test_resend_after_the_cooldown_sends_a_fresh_code(self, client):
        token = self._signup(client)
        first = _code_from_last_mail()
        sent = len(mail.outbox)
        self._end_cooldown()

        response = _post(client, "/auth/email/verify/resend", token=token)

        assert response.status_code == status.HTTP_200_OK
        assert len(mail.outbox) == sent + 1
        assert _code_from_last_mail() != first

    def test_resend_is_capped_per_pending_flow(self, client, settings):
        token = self._signup(client)
        quota = settings.ACCOUNT_EMAIL_VERIFICATION_SUPPORTS_RESEND

        for _ in range(quota):
            self._end_cooldown()
            response = _post(client, "/auth/email/verify/resend", token=token)
            assert response.status_code == status.HTTP_200_OK

        self._end_cooldown()
        response = _post(client, "/auth/email/verify/resend", token=token)
        assert response.status_code == status.HTTP_409_CONFLICT

    def test_a_mistyped_address_can_be_corrected_mid_verification(self, client):
        token = self._signup(client, email="typo@exmaple.com")
        self._end_cooldown()

        response = _post(
            client,
            "/account/email",
            {"email": "right@example.com"},
            token=token,
        )

        assert response.status_code == status.HTTP_200_OK, response.content
        assert mail.outbox[-1].to == ["right@example.com"]
        code = _code_from_last_mail()
        verified = _post(
            client, "/auth/email/verify", {"key": code}, token=token
        )
        assert verified.status_code == status.HTTP_200_OK, verified.content

    def test_the_address_can_only_be_changed_a_bounded_number_of_times(
        self, client, settings
    ):
        token = self._signup(client, email="typo@exmaple.com")
        quota = settings.ACCOUNT_EMAIL_VERIFICATION_SUPPORTS_CHANGE

        for n in range(quota):
            self._end_cooldown()
            response = _post(
                client,
                "/account/email",
                {"email": f"try{n}@example.com"},
                token=token,
            )
            assert response.status_code == status.HTTP_200_OK

        response = _post(
            client,
            "/account/email",
            {"email": "one.too.many@example.com"},
            token=token,
        )
        assert response.status_code == status.HTTP_409_CONFLICT


def test_the_verification_cooldown_reaches_allauth(settings):
    """allauth's own default is 1/10s; the storefront timer mirrors ours."""
    from allauth.account import app_settings

    cooldown = settings.ACCOUNT_RESEND_COOLDOWN_SECONDS
    assert app_settings.RATE_LIMITS["confirm_email"] == f"1/{cooldown}s/key"


@pytest.mark.django_db
class TestLoginCodeResendIsThrottledPerIp:
    """allauth rate-limits neither the login-code resend nor its mail, so
    ``AllAuthRateLimitMiddleware`` is the only brake on it."""

    def test_the_resend_route_has_a_ceiling(self, settings):
        from core.middleware.allauth_ratelimit import AllAuthRateLimitMiddleware

        settings.DEBUG = False
        settings.DISABLE_CACHE = False
        cache.clear()
        middleware = AllAuthRateLimitMiddleware(
            lambda request: HttpResponse(status=status.HTTP_200_OK)
        )
        request = RequestFactory().post(f"{BASE}/auth/code/resend")

        codes = [middleware(request).status_code for _ in range(5)]

        assert codes[:3] == [status.HTTP_200_OK] * 3
        assert codes[3:] == [status.HTTP_429_TOO_MANY_REQUESTS] * 2


@pytest.mark.django_db
class TestCooldownIsPublished:
    """The storefront timer reads the number allauth enforces."""

    def test_tenant_config_carries_the_cooldown(self, settings):
        from tenant.serializers import TenantConfigSerializer
        from tests.utils.staff import store_tenant

        settings.ACCOUNT_RESEND_COOLDOWN_SECONDS = 45

        data = TenantConfigSerializer(store_tenant("cooldown_store")).data

        assert data["code_resend_cooldown_seconds"] == 45

    def test_the_field_is_optional_for_a_frontend_first_deploy(self):
        from tenant.serializers import TenantConfigSerializer

        field = TenantConfigSerializer().fields["code_resend_cooldown_seconds"]
        assert field.required is False
        assert field.read_only is False
