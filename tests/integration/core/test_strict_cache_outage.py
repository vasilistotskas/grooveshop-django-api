"""Redis down, through the whole stack: every route refuses the same way.

A security control that cannot reach its strict cache key (a rate limit, an
IdP code, a single-use ticket) raises instead of lifting itself, and
``core.middleware.strict_cache`` answers 503 ``temporarily_unavailable`` in
the shape the route's client reads. These send real requests through the
real middleware with the default cache pointed at a port nothing listens
on, so the answer is the one a client would get, not a unit's.
"""

from __future__ import annotations

import json
from unittest import mock

import pytest
from django.conf import settings
from django.test import Client, override_settings
from redis.backoff import NoBackoff
from redis.retry import Retry
from rest_framework.test import APIClient

from core.caches import STRICT_CACHE_UNAVAILABLE_MESSAGE
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

PASSWORD = "S3cure-pass-phrase!"
CODE = "temporarily_unavailable"


@pytest.fixture
def redis_down():
    stopped = {
        **settings.CACHES["default"],
        "BACKEND": "core.caches.CustomCache",
        "LOCATION": "redis://127.0.0.1:1/0",
        "OPTIONS": {
            "retry": Retry(NoBackoff(), 0),
            "socket_connect_timeout": 0.2,
            "health_check_interval": 0,
        },
    }
    with override_settings(CACHES={"default": stopped}):
        yield


@pytest.fixture
def user():
    return UserAccountFactory(plain_password=PASSWORD)


def test_allauths_api_answers_its_own_envelope(redis_down, user):
    response = Client().post(
        "/_allauth/app/v1/auth/login",
        data=json.dumps({"email": user.email, "password": PASSWORD}),
        content_type="application/json",
    )

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == 503
    assert [error["code"] for error in body["errors"]] == [CODE]


def test_the_idp_token_endpoint_answers_oauths_error(redis_down, monkeypatch):
    from allauth.idp.oidc.models import Client as OIDCClient

    # The main suite runs in the public schema, the platform's (staff)
    # issuer, which wants a store's staff MCP server as the resource; a
    # store's issuer takes a code exchange without one.
    monkeypatch.setattr("agent.oidc.on_platform_issuer", lambda: False)
    monkeypatch.setattr(
        "agent.oidc.store_mcp_resource", lambda: "https://store.example/mcp"
    )

    client = OIDCClient.objects.create(
        name="Outage agent", type=OIDCClient.Type.PUBLIC
    )

    response = Client().post(
        "/identity/o/api/token",
        data={
            "grant_type": "authorization_code",
            "client_id": client.id,
            "code": "a-code-only-redis-knows",
            "redirect_uri": "https://agent.example/callback",
        },
    )

    assert response.status_code == 503
    body = response.json()
    assert body["error"] == CODE
    assert body["error_description"]


def test_an_accounts_page_answers_an_html_page(redis_down, user):
    response = Client().post(
        "/accounts/login/", {"login": user.email, "password": PASSWORD}
    )

    assert response.status_code == 503
    assert response["Content-Type"].startswith("text/html")
    assert CODE in response.content.decode()


def test_a_drf_route_answers_the_error_contract(redis_down, user):
    from knox.models import AuthToken

    # The ticket is minted for a caller with a live session.
    AuthToken.objects.create(user)
    client = APIClient()
    client.force_authenticate(user)

    response = client.post("/api/v1/websocket/ticket")

    assert response.status_code == 503
    assert response.json() == {
        "detail": str(STRICT_CACHE_UNAVAILABLE_MESSAGE),
        "code": CODE,
    }


def test_the_refusal_is_logged_with_the_failure_s_traceback(
    redis_down, user, caplog, monkeypatch
):
    """The middleware answers any Redis outage a view raises, strict keys
    or not: the logged line carries the traceback that names the caller."""
    from core.middleware import strict_cache

    monkeypatch.setattr(strict_cache._refused, "_last_logged", float("-inf"))

    with caplog.at_level("ERROR", logger="core.middleware.strict_cache"):
        response = Client().post(
            "/accounts/login/", {"login": user.email, "password": PASSWORD}
        )

    assert response.status_code == 503
    (record,) = [r for r in caplog.records if r.name == strict_cache.__name__]
    assert record.exc_info is not None


class TestVivaHandshake:
    """Viva's webhook GET answers with the store's verification key. Its
    per-address budget fails open only while the key is stored (answering
    is free); without one every answer calls Viva with the store's
    credentials, so the budget fails closed."""

    def _get(self, monkeypatch, verification_key):
        from tests.utils.viva import CREDENTIALS

        fetch = mock.Mock(return_value="fetched-key")
        monkeypatch.setattr(
            "tenant.credentials.viva_wallet_credentials",
            lambda: {
                **CREDENTIALS,
                "webhook_verification_key": verification_key,
            },
        )
        monkeypatch.setattr(
            "order.views.viva_webhook._fetch_verification_key", fetch
        )
        return Client().get("/viva-wallet/webhook/"), fetch

    def test_a_stored_key_is_still_answered(self, redis_down, monkeypatch):
        response, fetch = self._get(monkeypatch, "stored-key")

        assert response.status_code == 200
        assert response.json() == {"Key": "stored-key"}
        fetch.assert_not_called()

    def test_without_a_stored_key_viva_is_not_called(
        self, redis_down, monkeypatch
    ):
        response, fetch = self._get(monkeypatch, "")

        assert response.status_code == 503
        assert CODE in response.content.decode()
        fetch.assert_not_called()
