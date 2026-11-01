"""The webhook's signature check: the Standard Webhooks library behind a
thin adapter for Resend's ``svix-*`` header names.

The vector was produced by the ``svix`` package itself (``Webhook.sign``), so
the adapter is pinned against the provider's own signer, not ours.
"""

from __future__ import annotations

from datetime import datetime
from unittest import mock

import pytest

from emails.webhooks import InvalidSignature, verify_signature

SECRET = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
MESSAGE_ID = "msg_test123"
TIMESTAMP = 1793558167
BODY = b'{"type":"email.delivered","data":{"email_id":"abc"}}'
SIGNATURE = "v1,rlnQY4R0Trqn1P8PWxUACwaJgW1K4CNFYUZ0XiyXKdE="


def _headers(**overrides) -> dict[str, str]:
    return {
        "svix-id": MESSAGE_ID,
        "svix-timestamp": str(TIMESTAMP),
        "svix-signature": SIGNATURE,
        **overrides,
    }


def _verify(headers=None, body=BODY, secret=SECRET, now=TIMESTAMP):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(now, tz)

    with mock.patch("standardwebhooks.webhooks.datetime", Clock):
        return verify_signature(secret, headers or _headers(), body)


def test_a_signature_made_by_the_svix_package_verifies():
    assert _verify() == MESSAGE_ID


def test_one_changed_byte_is_refused():
    with pytest.raises(InvalidSignature):
        _verify(body=BODY.replace(b"abc", b"abd"))


@pytest.mark.parametrize("offset", [-301, 301])
def test_a_stale_or_future_timestamp_is_refused(offset):
    with pytest.raises(InvalidSignature):
        _verify(now=TIMESTAMP + offset)


@pytest.mark.parametrize("offset", [-299, 0, 299])
def test_a_timestamp_inside_the_five_minutes_passes(offset):
    assert _verify(now=TIMESTAMP + offset) == MESSAGE_ID


def test_another_secret_is_refused():
    with pytest.raises(InvalidSignature):
        _verify(secret="whsec_" + "QUJD" * 8)


@pytest.mark.parametrize("secret", ["whsec_not base64!", "whsec_", ""])
def test_a_malformed_or_empty_secret_is_refused_not_a_crash(secret):
    with pytest.raises(InvalidSignature):
        _verify(secret=secret)


@pytest.mark.parametrize(
    "missing", ["svix-id", "svix-timestamp", "svix-signature"]
)
def test_a_missing_header_is_refused(missing):
    headers = _headers()
    del headers[missing]
    with pytest.raises(InvalidSignature):
        _verify(headers=headers)


@pytest.mark.parametrize(
    "signature", ["garbage", "v1", "v1,", "v1,!!!", "v2,AAAA", ",,,"]
)
def test_a_malformed_signature_is_refused_not_a_crash(signature):
    with pytest.raises(InvalidSignature):
        _verify(headers=_headers(**{"svix-signature": signature}))


def test_a_timestamp_that_is_not_a_number_is_refused():
    with pytest.raises(InvalidSignature):
        _verify(headers=_headers(**{"svix-timestamp": "yesterday"}))


def test_a_body_that_is_not_utf8_is_refused():
    with pytest.raises(InvalidSignature):
        _verify(body=b"\xff\xfe")


def test_any_one_of_several_signatures_is_enough():
    """While a secret rotates the header carries a signature per secret."""
    headers = _headers(**{"svix-signature": f"v1,AAAA {SIGNATURE} v1,BBBB"})
    assert _verify(headers=headers) == MESSAGE_ID
