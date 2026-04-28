"""Unit tests for BoxNowClient.

All external HTTP calls are mocked with unittest.mock; no network I/O.
The Django cache is the in-process LocMemCache configured in conftest.py.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
import requests

from shipping_boxnow.client import BoxNowClient
from shipping_boxnow.exceptions import (
    BoxNowAPIError,
    BoxNowConfigError,
    BoxNowRetryableError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code: int, body=None, content: bytes | None = None):
    """Build a minimal requests.Response mock."""
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = status_code < 400

    if content is not None:
        resp.content = content
        resp.text = content.decode("utf-8", errors="replace")
        resp.json.side_effect = Exception("binary content")
    elif body is not None:
        payload = json.dumps(body).encode()
        resp.content = payload
        resp.text = json.dumps(body)
        resp.json.return_value = body
    else:
        resp.content = b""
        resp.text = ""
        resp.json.return_value = {}
    return resp


def _make_client(**kwargs) -> BoxNowClient:
    """Return a BoxNowClient with test credentials and a mock session."""
    session = MagicMock()
    defaults = {
        "client_id": "test-client-id",
        "client_secret": "test-client-secret",
        "partner_id": "99999",
        "api_base_url": "https://api-stage.boxnow.gr",
        "location_api_base_url": "https://locationapi-stage.boxnow.gr",
        "session": session,
    }
    defaults.update(kwargs)
    return BoxNowClient(**defaults)


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------


class TestAuthenticate:
    """Tests for BoxNowClient._get_access_token.

    Each test owns its cache state. The conftest ``clear_caches`` fixture
    clears AFTER yield, so a stale entry written by a previous test in the
    same xdist worker would still be visible at the START of the next
    test. Each test here calls ``cache.delete(_token_cache_key())`` at the
    top to make the starting cache state explicit and worker-order
    independent.
    """

    @staticmethod
    def _token_cache_key(client) -> str:
        return f"boxnow:access_token:{client.partner_id}"

    @pytest.mark.django_db
    def test_authenticate_success(self):
        """Happy path: POST /auth-sessions returns token; client caches it."""
        from django.core.cache import cache

        client = _make_client()
        cache.delete(self._token_cache_key(client))
        auth_resp = _make_response(
            200,
            {
                "access_token": "abc123",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )
        client._session.post.return_value = auth_resp

        token = client._get_access_token()

        assert token == "abc123"
        cached = cache.get(self._token_cache_key(client))
        assert cached == "abc123"
        client._session.post.assert_called_once()
        call_kwargs = client._session.post.call_args
        assert "/api/v1/auth-sessions" in call_kwargs[0][0]

    @pytest.mark.django_db
    def test_authenticate_uses_cache(self):
        """When a valid token is cached, no HTTP call should be made."""
        from django.core.cache import cache

        client = _make_client()
        cache.set(self._token_cache_key(client), "cached-token", 60)

        token = client._get_access_token()

        assert token == "cached-token"
        client._session.post.assert_not_called()

    @pytest.mark.django_db
    def test_authenticate_force_refresh(self):
        """force_refresh=True bypasses cache and fetches a fresh token."""
        from django.core.cache import cache

        client = _make_client()
        cache.set(self._token_cache_key(client), "old-token", 3600)
        auth_resp = _make_response(
            200,
            {
                "access_token": "fresh-token",
                "token_type": "Bearer",
                "expires_in": 3600,
            },
        )
        client._session.post.return_value = auth_resp

        token = client._get_access_token(force_refresh=True)

        assert token == "fresh-token"
        client._session.post.assert_called_once()


# ---------------------------------------------------------------------------
# Request helper tests
# ---------------------------------------------------------------------------


class TestRequest:
    """Tests for BoxNowClient._request."""

    @pytest.mark.django_db
    def test_request_attaches_bearer_header(self):
        """_request must set Authorization: Bearer <token>."""
        client = _make_client()
        auth_resp = _make_response(
            200,
            {"access_token": "mytoken", "expires_in": 3600},
        )
        api_resp = _make_response(200, {"ok": True})

        client._session.post.return_value = auth_resp
        client._session.request.return_value = api_resp

        client._request("GET", "/api/v1/test")

        _, call_kwargs = client._session.request.call_args
        headers = call_kwargs["headers"]
        assert headers["Authorization"] == "Bearer mytoken"

    @pytest.mark.django_db
    def test_request_401_refreshes_token_and_retries(self):
        """On 401 response, client refreshes token once and retries."""
        client = _make_client()
        auth_resp_1 = _make_response(
            200, {"access_token": "old-tok", "expires_in": 3600}
        )
        auth_resp_2 = _make_response(
            200, {"access_token": "new-tok", "expires_in": 3600}
        )
        resp_401 = _make_response(401, {"error": "Unauthorized"})
        resp_200 = _make_response(200, {"result": "ok"})

        # First auth returns old token; second returns new token.
        client._session.post.side_effect = [auth_resp_1, auth_resp_2]
        # First request gets 401; retry gets 200.
        client._session.request.side_effect = [resp_401, resp_200]

        result = client._request("GET", "/api/v1/test")

        assert result.status_code == 200
        # Two POST calls to auth-sessions (initial + refresh on 401).
        assert client._session.post.call_count == 2
        # Two request calls (initial + retry).
        assert client._session.request.call_count == 2

    @pytest.mark.django_db
    def test_request_5xx_raises_retryable_error(self):
        """HTTP 502 response raises BoxNowRetryableError."""
        client = _make_client()
        auth_resp = _make_response(
            200, {"access_token": "tok", "expires_in": 3600}
        )
        resp_502 = _make_response(502, {"error": "Bad Gateway"})
        resp_502.ok = False

        client._session.post.return_value = auth_resp
        client._session.request.return_value = resp_502

        with pytest.raises(BoxNowRetryableError) as exc_info:
            client._request("GET", "/api/v1/test")

        assert exc_info.value.status_code == 502

    @pytest.mark.django_db
    def test_request_4xx_raises_api_error_with_code(self):
        """HTTP 400 with P405 code raises BoxNowAPIError with that code."""
        client = _make_client()
        auth_resp = _make_response(
            200, {"access_token": "tok", "expires_in": 3600}
        )
        resp_400 = _make_response(
            400, {"code": "P405", "message": "Invalid phone"}
        )
        resp_400.ok = False

        client._session.post.return_value = auth_resp
        client._session.request.return_value = resp_400

        with pytest.raises(BoxNowAPIError) as exc_info:
            client._request("GET", "/api/v1/test")

        err = exc_info.value
        assert err.code == "P405"
        assert "Invalid phone" in err.message
        assert err.status_code == 400


# ---------------------------------------------------------------------------
# Public method tests
# ---------------------------------------------------------------------------


class TestCreateDeliveryRequest:
    """Test create_delivery_request serialises body as JSON with camelCase."""

    @pytest.mark.django_db
    def test_create_delivery_request_happy_path(self):
        """create_delivery_request returns the parsed response dict."""
        client = _make_client()
        auth_resp = _make_response(
            200, {"access_token": "tok", "expires_in": 3600}
        )
        api_resp = _make_response(
            200,
            {
                "id": "42224",
                "parcels": [{"id": "9219709201"}],
            },
        )
        client._session.post.return_value = auth_resp
        client._session.request.return_value = api_resp

        payload = {
            "orderNumber": "ORD-001",
            "paymentMode": "prepaid",
            "amountToBeCollected": "0.00",
            "origin": {"locationId": "2", "contactName": "Shop"},
            "destination": {"locationId": "4", "contactEmail": "c@test.com"},
            "items": [{"id": "1", "compartmentSize": 1, "weight": 0}],
        }
        result = client.create_delivery_request(payload)

        assert result["id"] == "42224"
        assert result["parcels"][0]["id"] == "9219709201"

        # Verify body was sent as camelCase JSON.
        _, req_kwargs = client._session.request.call_args
        sent_json = req_kwargs["json"]
        assert "orderNumber" in sent_json
        assert "paymentMode" in sent_json


class TestCancelParcel:
    """Test cancel_parcel sends the right request."""

    @pytest.mark.django_db
    def test_cancel_parcel_success(self):
        """cancel_parcel makes POST to /api/v1/parcels/{id}:cancel."""
        client = _make_client()
        auth_resp = _make_response(
            200, {"access_token": "tok", "expires_in": 3600}
        )
        api_resp = _make_response(200)
        client._session.post.return_value = auth_resp
        client._session.request.return_value = api_resp

        client.cancel_parcel("9219709201")  # must not raise

        # Verify the request was made to the cancel endpoint.
        call_args_str = str(client._session.request.call_args)
        assert ":cancel" in call_args_str


class TestFetchParcelLabel:
    """Test fetch_parcel_label returns raw bytes."""

    @pytest.mark.django_db
    def test_fetch_parcel_label_returns_bytes(self):
        """fetch_parcel_label returns the raw response bytes."""
        pdf_bytes = b"%PDF-1.4 mock content"
        client = _make_client()
        auth_resp = _make_response(
            200, {"access_token": "tok", "expires_in": 3600}
        )
        api_resp = _make_response(200, content=pdf_bytes)
        client._session.post.return_value = auth_resp
        client._session.request.return_value = api_resp

        result = client.fetch_parcel_label("9219709201")

        assert result == pdf_bytes
        assert isinstance(result, bytes)


class TestConfigError:
    """Test BoxNowConfigError raised when credentials missing."""

    def test_config_error_when_credentials_missing(self, settings):
        """Instantiating without credentials raises BoxNowConfigError."""
        settings.BOXNOW_CLIENT_ID = ""
        settings.BOXNOW_CLIENT_SECRET = ""
        settings.BOXNOW_PARTNER_ID = ""

        with pytest.raises(BoxNowConfigError) as exc_info:
            BoxNowClient()

        assert "BOXNOW_CLIENT_ID" in str(exc_info.value)
