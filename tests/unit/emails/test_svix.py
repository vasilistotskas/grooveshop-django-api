"""Svix signature verification: the scheme Resend signs its webhooks with."""

from __future__ import annotations

import pytest

from emails import svix

# Produced by the ``svix`` package itself (``Webhook.sign``), so the scheme
# is pinned against the reference implementation, not against our own signer.
SECRET = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
MESSAGE_ID = "msg_test123"
TIMESTAMP = 1793558167
BODY = b'{"type":"email.delivered","data":{"email_id":"abc"}}'
SIGNATURE = "v1,rlnQY4R0Trqn1P8PWxUACwaJgW1K4CNFYUZ0XiyXKdE="

TOLERANCE = 300


def _headers(**overrides) -> dict[str, str]:
    return {
        "svix-id": MESSAGE_ID,
        "svix-timestamp": str(TIMESTAMP),
        "svix-signature": SIGNATURE,
        **overrides,
    }


def _verify(headers=None, body=BODY, now=TIMESTAMP, secret=SECRET):
    return svix.verify(
        secret,
        headers or _headers(),
        body,
        tolerance_seconds=TOLERANCE,
        now=now,
    )


def test_a_signature_made_by_the_svix_package_verifies():
    assert _verify() == MESSAGE_ID


def test_our_signer_agrees_with_the_svix_package():
    assert svix.sign(SECRET, MESSAGE_ID, TIMESTAMP, BODY) == SIGNATURE


def test_one_changed_byte_is_refused():
    with pytest.raises(svix.InvalidSignature):
        _verify(body=BODY.replace(b"abc", b"abd"))


def test_a_parsed_and_reencoded_body_is_refused():
    with pytest.raises(svix.InvalidSignature):
        _verify(
            body=b'{ "type": "email.delivered", "data": {"email_id": "abc"}}'
        )


@pytest.mark.parametrize("offset", [-TOLERANCE - 1, TOLERANCE + 1])
def test_a_stale_or_future_timestamp_is_refused(offset):
    with pytest.raises(svix.InvalidSignature):
        _verify(now=TIMESTAMP + offset)


@pytest.mark.parametrize("offset", [-TOLERANCE, 0, TOLERANCE])
def test_a_timestamp_inside_the_tolerance_passes(offset):
    assert _verify(now=TIMESTAMP + offset) == MESSAGE_ID


def test_another_secret_is_refused():
    with pytest.raises(svix.InvalidSignature):
        _verify(secret="whsec_" + "QUJD" * 8)


def test_a_malformed_secret_is_refused_not_a_crash():
    with pytest.raises(svix.InvalidSignature):
        _verify(secret="whsec_not base64!")


@pytest.mark.parametrize(
    "missing", ["svix-id", "svix-timestamp", "svix-signature"]
)
def test_a_missing_header_is_refused(missing):
    headers = _headers()
    del headers[missing]
    with pytest.raises(svix.InvalidSignature):
        _verify(headers=headers)


def test_a_timestamp_that_is_not_a_number_is_refused():
    with pytest.raises(svix.InvalidSignature):
        _verify(headers=_headers(**{"svix-timestamp": "yesterday"}))


def test_any_one_of_several_signatures_is_enough():
    """While a secret rotates the header carries a signature per secret."""
    headers = _headers(**{"svix-signature": f"v1,AAAA {SIGNATURE} v1,BBBB"})
    assert _verify(headers=headers) == MESSAGE_ID


def test_a_signature_of_another_version_is_not_accepted():
    headers = _headers(**{"svix-signature": SIGNATURE.replace("v1,", "v2,")})
    with pytest.raises(svix.InvalidSignature):
        _verify(headers=headers)
