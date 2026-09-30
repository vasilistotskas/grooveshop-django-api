"""HTTP error mapping, token lifecycle and request shaping of BoxNowClient.

Covers ``shipping_boxnow/client.py`` paths ``test_client.py`` leaves open:
auth-endpoint failures, token TTL, 401-after-refresh and 403 handling,
error-envelope parsing (``errorCode`` / ``error`` / non-JSON), transport
errors, ``_json`` fallbacks, and the query/body shaping of the location,
parcel, label and delivery-request calls. The ``requests`` session is the
only thing mocked.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import requests
from django.core.cache.backends.locmem import LocMemCache
from requests.adapters import HTTPAdapter

from shipping_boxnow.client import BoxNowClient
from shipping_boxnow.exceptions import (
    BoxNowAPIError,
    BoxNowAuthError,
    BoxNowRetryableError,
)

_API = "https://api-stage.boxnow.gr"
_LOCATIONS = "https://locationapi-stage.boxnow.gr"


def _response(status_code: int, body=None, *, text: str | None = None):
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.ok = status_code < 400
    if text is not None:
        resp.content = text.encode()
        resp.text = text
        resp.json.side_effect = ValueError("not json")
    elif body is not None:
        resp.content = json.dumps(body).encode()
        resp.text = json.dumps(body)
        resp.json.return_value = body
    else:
        resp.content = b""
        resp.text = ""
        resp.json.side_effect = ValueError("empty")
    return resp


def _token(value: str = "tok", expires_in: int | None = 3600):
    body = {"access_token": value}
    if expires_in is not None:
        body["expires_in"] = expires_in
    return _response(200, body)


def _client(**kwargs) -> BoxNowClient:
    defaults = {
        "client_id": "cid",
        "client_secret": "csecret",
        "partner_id": 4242,
        "api_base_url": f"{_API}/",
        "location_api_base_url": f"{_LOCATIONS}/",
        "timeout": 7,
        "session": MagicMock(),
    }
    defaults.update(kwargs)
    return BoxNowClient(**defaults)


@pytest.fixture(autouse=True)
def cache():
    """A private LocMemCache — the shared Redis is cleared by other
    xdist workers (see ``test_client.py``)."""
    local = LocMemCache(f"boxnow-error-mapping-{uuid4()}", {})
    with patch("django.core.cache.cache", local):
        yield local


class TestConstruction:
    def test_base_urls_are_normalised_and_partner_id_is_a_string(self):
        client = _client()

        assert client.api_base_url == _API
        assert client.location_api_base_url == _LOCATIONS
        assert client.partner_id == "4242"
        assert client._token_cache_key == "boxnow:access_token:4242"

    def test_default_session_mounts_the_retrying_adapter(self):
        client = _client(session=None)

        for prefix in ("https://", "http://"):
            adapter = client._session.adapters[prefix]
            assert isinstance(adapter, HTTPAdapter)
            assert adapter.max_retries.total == 3
            assert adapter.max_retries.status_forcelist == [
                500,
                502,
                503,
                504,
            ]


class TestTokenLifecycle:
    def test_auth_endpoint_rejection_raises_auth_error(self):
        client = _client()
        client._session.post.return_value = _response(
            400, {"error": "invalid_client"}
        )

        with pytest.raises(BoxNowAuthError) as exc_info:
            client._get_access_token()

        assert exc_info.value.status_code == 400
        assert exc_info.value.message == "BoxNow authentication failed"
        assert exc_info.value.response_text == '{"error": "invalid_client"}'

    def test_auth_connection_error_is_retryable(self):
        client = _client()
        client._session.post.side_effect = requests.ConnectionError("dns")

        with pytest.raises(BoxNowRetryableError) as exc_info:
            client._get_access_token()

        assert exc_info.value.status_code == 0

    def test_token_request_carries_client_credentials_and_timeout(self):
        client = _client()
        client._session.post.return_value = _token()

        client._get_access_token()

        client._session.post.assert_called_once_with(
            f"{_API}/api/v1/auth-sessions",
            json={
                "grant_type": "client_credentials",
                "client_id": "cid",
                "client_secret": "csecret",
            },
            timeout=7,
        )

    @pytest.mark.parametrize(
        ("expires_in", "ttl"),
        [(3600, 3540), (90, 60), (30, 60), (None, 3540)],
        ids=["hour", "floor-edge", "below-floor", "default"],
    )
    def test_token_is_cached_a_minute_short_of_expiry(
        self, cache, expires_in, ttl
    ):
        client = _client()
        client._session.post.return_value = _token("fresh", expires_in)

        with patch.object(cache, "set", wraps=cache.set) as cache_set:
            assert client._get_access_token() == "fresh"

        cache_set.assert_called_once_with(
            "boxnow:access_token:4242", "fresh", timeout=ttl
        )


class TestResponseMapping:
    def _authed(self, *responses) -> BoxNowClient:
        client = _client()
        client._session.post.return_value = _token()
        client._session.request.side_effect = list(responses)
        return client

    def test_second_401_after_refresh_raises_auth_error(self):
        client = self._authed(
            _response(401, {"message": "expired"}),
            _response(401, {"message": "still expired"}),
        )

        with pytest.raises(BoxNowAuthError) as exc_info:
            client._request("GET", "/api/v1/parcels")

        assert exc_info.value.status_code == 401
        assert exc_info.value.message == "still expired"
        assert client._session.request.call_count == 2
        # Initial fetch + exactly one forced refresh.
        assert client._session.post.call_count == 2

    def test_403_raises_auth_error_without_refreshing(self):
        client = self._authed(
            _response(403, {"code": "P401", "message": "Forbidden"})
        )

        with pytest.raises(BoxNowAuthError) as exc_info:
            client._request("GET", "/api/v1/parcels")

        assert exc_info.value.code == "P401"
        assert client._session.post.call_count == 1
        assert client._session.request.call_count == 1

    def test_alternate_envelope_keys_and_details_are_parsed(self):
        client = self._authed(
            _response(
                422,
                {
                    "errorCode": "P422",
                    "error": "Address not found",
                    "field": "postalCode",
                },
            )
        )

        with pytest.raises(BoxNowAPIError) as exc_info:
            client._request("POST", "/api/v2/x")

        err = exc_info.value
        assert type(err) is BoxNowAPIError
        assert (err.status_code, err.code, err.message) == (
            422,
            "P422",
            "Address not found",
        )
        assert err.details == {"field": "postalCode"}
        assert str(err) == "BoxNow API 422 [P422]: Address not found"

    def test_non_json_error_body_uses_the_truncated_text(self):
        html = "<html>" + "x" * 300 + "</html>"
        client = self._authed(_response(404, text=html))

        with pytest.raises(BoxNowAPIError) as exc_info:
            client._request("GET", "/api/v1/parcels/1/label.pdf")

        err = exc_info.value
        assert err.code is None
        assert err.message == html[:200]
        assert err.details == {}
        assert err.response_text == html

    def test_json_error_without_message_falls_back_to_text(self):
        client = self._authed(_response(400, {"code": "P400"}))

        with pytest.raises(BoxNowAPIError) as exc_info:
            client._request("GET", "/api/v1/parcels")

        assert exc_info.value.message == '{"code": "P400"}'

    def test_request_connection_error_is_retryable(self):
        client = _client()
        client._session.post.return_value = _token()
        client._session.request.side_effect = requests.ConnectionError("rst")

        with pytest.raises(BoxNowRetryableError) as exc_info:
            client._request("GET", "/api/v1/parcels")

        assert exc_info.value.status_code == 0
        assert "BoxNow connection error" in exc_info.value.message

    def test_timeout_message_names_the_configured_timeout(self):
        client = _client()
        client._session.post.return_value = _token()
        client._session.request.side_effect = requests.ReadTimeout("slow")

        with pytest.raises(BoxNowRetryableError) as exc_info:
            client._request("GET", "/api/v1/parcels")

        assert exc_info.value.status_code == 0
        assert exc_info.value.message == "BoxNow timeout after 7s: slow"

    def test_extra_headers_are_merged_with_the_bearer_token(self):
        client = self._authed(_response(200, {}))

        client._request(
            "get",
            "/api/v1/parcels",
            params={"q": "1"},
            headers={"Accept-Language": "el"},
        )

        client._session.request.assert_called_once_with(
            method="GET",
            url=f"{_API}/api/v1/parcels",
            json=None,
            params={"q": "1"},
            headers={
                "Authorization": "Bearer tok",
                "Accept-Language": "el",
            },
            timeout=7,
        )


class TestJsonFallbacks:
    def test_empty_body_is_an_empty_dict(self):
        assert _client()._json(_response(200)) == {}

    def test_unparseable_body_is_an_empty_dict(self):
        assert _client()._json(_response(200, text="not json")) == {}


class TestEndpointShaping:
    def _ok(self, body=None) -> BoxNowClient:
        client = _client()
        client._session.post.return_value = _token()
        client._session.request.return_value = _response(200, body or {})
        return client

    def _sent(self, client):
        return client._session.request.call_args.kwargs

    def test_list_destinations_uses_location_api_and_camel_params(self):
        client = self._ok({"data": [{"id": "apm-1"}]})

        result = client.list_destinations(
            latlng="37.9,23.7",
            radius=5000,
            required_size=2,
            location_type=["apm", "any-apm"],
            name="Syntagma",
        )

        assert result == [{"id": "apm-1"}]
        sent = self._sent(client)
        assert sent["url"] == f"{_LOCATIONS}/api/v1/destinations"
        assert sent["params"] == {
            "latlng": "37.9,23.7",
            "radius": 5000,
            "requiredSize": 2,
            "locationType": "apm,any-apm",
            "name": "Syntagma",
        }

    def test_list_origins_without_filters_sends_no_params(self):
        client = self._ok({})

        assert client.list_origins() == []
        sent = self._sent(client)
        assert sent["url"] == f"{_LOCATIONS}/api/v1/origins"
        assert sent["params"] is None

    def test_list_origins_accepts_a_single_location_type(self):
        client = self._ok({"data": []})

        client.list_origins(location_type="warehouse")

        assert self._sent(client)["params"] == {"locationType": "warehouse"}

    def test_get_parcel_info_maps_every_filter(self):
        envelope = {"pagination": {}, "count": 0, "data": []}
        client = self._ok(envelope)

        result = client.get_parcel_info(
            parcel_id="9200000001",
            order_number="ORD-1",
            q="maria",
            payment_state="pending",
            payment_mode="cod",
            state=["new", "intransit"],
            page_token="next",
            limit=100,
        )

        assert result == envelope
        sent = self._sent(client)
        assert sent["url"] == f"{_API}/api/v1/parcels"
        assert sent["params"] == {
            "parcelId": "9200000001",
            "orderNumber": "ORD-1",
            "q": "maria",
            "paymentState": "pending",
            "paymentMode": "cod",
            "state": ["new", "intransit"],
            "pageToken": "next",
            "limit": 100,
        }

    def test_find_closest_locker_posts_the_address(self):
        client = self._ok({"id": "apm-9", "distance": 120})

        result = client.find_closest_locker(
            city="Athens", street="Ermou 1", postal_code="10563"
        )

        assert result == {"id": "apm-9", "distance": 120}
        sent = self._sent(client)
        assert sent["method"] == "POST"
        assert sent["url"] == (
            f"{_API}/api/v2/delivery-requests:checkAddressDelivery"
        )
        assert sent["json"] == {
            "city": "Athens",
            "street": "Ermou 1",
            "postalCode": "10563",
            "region": "el-GR",
            "compartmentSize": 1,
        }

    def test_update_delivery_request_puts_allow_return(self):
        client = self._ok({"id": "777"})

        assert client.update_delivery_request("777", allow_return=False) == {
            "id": "777"
        }
        sent = self._sent(client)
        assert (sent["method"], sent["url"], sent["json"]) == (
            "PUT",
            f"{_API}/api/v1/delivery-requests/777",
            {"allowReturn": False},
        )

    def test_fetch_order_label_requests_zpl_at_the_given_dpi(self):
        client = _client()
        client._session.post.return_value = _token()
        label = _response(200, text="^XA^XZ")
        client._session.request.return_value = label

        result = client.fetch_order_label("ORD-1", label_type="zpl", dpi=300)

        assert result == b"^XA^XZ"
        sent = self._sent(client)
        assert sent["url"] == f"{_API}/api/v1/delivery-requests/ORD-1/label.zpl"
        assert sent["params"] == {"dpi": 300}

    def test_list_delivery_partners_returns_the_data_array(self):
        client = self._ok({"data": [{"id": "p1", "name": "BoxNow"}]})

        assert client.list_delivery_partners() == [
            {"id": "p1", "name": "BoxNow"}
        ]
        assert self._sent(client)["url"] == f"{_API}/api/v1/delivery-partners"
