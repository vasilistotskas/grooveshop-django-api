"""``MetaCapiClient.send`` sorts a real ``FacebookRequestError``.

The SDK exposes ``http_status`` and ``body`` as METHODS, not attributes,
so these tests raise a genuine SDK exception from the pixel call rather
than a stub whose attributes would hide that difference. Only
``AdsPixel.create_event`` (the HTTP POST) is patched.
"""

from __future__ import annotations

import json
import time
from unittest import mock

import pytest
from facebook_business.adobjects.adspixel import AdsPixel
from facebook_business.adobjects.serverside.action_source import (
    ActionSource,
)
from facebook_business.adobjects.serverside.event import Event
from facebook_business.adobjects.serverside.user_data import UserData
from facebook_business.exceptions import FacebookRequestError

from meta_capi.client import MetaCapiClient
from meta_capi.exceptions import MetaCapiError, MetaCapiTransientError


def _request_error(status: int, error: dict) -> FacebookRequestError:
    return FacebookRequestError(
        message="Call was not successful",
        request_context={"method": "POST", "path": "/123/events"},
        http_status=status,
        http_headers={},
        body=json.dumps({"error": error}),
    )


def _send(exc: FacebookRequestError) -> None:
    event = Event(
        event_name="Purchase",
        event_time=int(time.time()),
        user_data=UserData(email="shopper@example.com"),
        action_source=ActionSource.WEBSITE,
    )
    client = MetaCapiClient(
        pixel_id="123", access_token="token", test_event_code=""
    )
    with mock.patch.object(AdsPixel, "create_event", side_effect=exc):
        client.send([event])


@pytest.mark.parametrize("status", [503, 500, 429, 408])
def test_server_error_and_rate_limit_are_transient(status):
    exc = _request_error(
        status,
        {"message": "Service temporarily unavailable", "fbtrace_id": "AbC"},
    )

    with pytest.raises(MetaCapiTransientError) as caught:
        _send(exc)

    assert str(caught.value) == (
        f"Meta CAPI {status}: Service temporarily unavailable (fbtrace=AbC)"
    )
    assert caught.value.__cause__ is exc


def test_client_error_is_permanent_with_trace_and_user_message():
    exc = _request_error(
        400,
        {
            "message": "Invalid parameter",
            "error_user_msg": "The pixel id is not valid",
            "fbtrace_id": "XyZ",
        },
    )

    with pytest.raises(MetaCapiError) as caught:
        _send(exc)

    assert not isinstance(caught.value, MetaCapiTransientError)
    assert str(caught.value) == (
        "Meta CAPI 400: The pixel id is not valid (fbtrace=XyZ)"
    )


def test_a_payload_the_sdk_cannot_normalise_is_permanent_and_unsent():
    """The SDK normalises while serialising, before any request. Its
    error names the offending value (customer PII), so it is kept out of
    the message, which is stored on the event log."""
    event = Event(
        event_name="Purchase",
        event_time=int(time.time()),
        user_data=UserData(phone="12"),
        action_source=ActionSource.WEBSITE,
    )
    client = MetaCapiClient(
        pixel_id="123", access_token="token", test_event_code=""
    )

    with (
        mock.patch.object(AdsPixel, "create_event") as create_event,
        pytest.raises(MetaCapiError) as caught,
    ):
        client.send([event])

    assert not isinstance(caught.value, MetaCapiTransientError)
    assert str(caught.value) == (
        "Meta CAPI payload rejected by the SDK normaliser (ValueError)"
    )
    assert isinstance(caught.value.__cause__, ValueError)
    create_event.assert_not_called()
