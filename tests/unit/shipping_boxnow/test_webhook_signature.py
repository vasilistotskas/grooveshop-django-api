"""Unit tests for shipping_boxnow.webhook — pure signature / extraction helpers.

These tests exercise the module with no Django dependency so they run fast
and without a DB fixture.
"""

from __future__ import annotations

import hashlib
import hmac
from unittest.mock import patch

import pytest

from shipping_boxnow.webhook import (
    BoxNowWebhookError,
    extract_data_substring,
    validate_envelope,
    verify_signature,
)


# ---------------------------------------------------------------------------
# extract_data_substring
# ---------------------------------------------------------------------------


class TestExtractDataSubstring:
    def test_simple_single_line_json(self):
        """Basic single-line payload with a flat data object."""
        raw = b'{"specversion":"1.0","data":{"parcelId":"9219709201","event":"new"},"id":"msg-1"}'
        result = extract_data_substring(raw)
        assert result == b'{"parcelId":"9219709201","event":"new"}'

    def test_with_escaped_quotes_inside_data(self):
        """Escaped quotes inside string values must not confuse brace tracking."""
        raw = b'{"specversion":"1.0","data":{"name":"hello \\"world\\"","event":"new"},"id":"x"}'
        result = extract_data_substring(raw)
        assert result == b'{"name":"hello \\"world\\"","event":"new"}'

    def test_nested_braces_in_data(self):
        """data value with nested objects is extracted in full."""
        raw = (
            b'{"id":"1","data":{"outer":{"inner":"v"},"arr":[1,2]},"other":"x"}'
        )
        result = extract_data_substring(raw)
        assert result == b'{"outer":{"inner":"v"},"arr":[1,2]}'

    def test_missing_data_key_raises_error(self):
        """Body without a 'data' key raises BoxNowWebhookError."""
        raw = b'{"specversion":"1.0","type":"gr.boxnow.parcel_event_change"}'
        with pytest.raises(BoxNowWebhookError, match='"data"'):
            extract_data_substring(raw)

    def test_empty_body_raises_error(self):
        """Empty body raises BoxNowWebhookError."""
        with pytest.raises(BoxNowWebhookError):
            extract_data_substring(b"")


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------


def _make_signature(data_bytes: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), data_bytes, hashlib.sha256).hexdigest()


class TestVerifySignature:
    def test_valid_signature_returns_true(self):
        """Known secret + payload → expected HMAC → True."""
        secret = "test-secret-key"
        data = b'{"parcelId":"9219709201","event":"new"}'
        sig = _make_signature(data, secret)

        assert verify_signature(data, sig, secret) is True

    def test_invalid_signature_returns_false(self):
        """Wrong signature returns False without raising."""
        data = b'{"parcelId":"9219709201","event":"new"}'
        assert verify_signature(data, "deadbeef" * 8, "test-secret") is False

    def test_uses_constant_time_compare(self):
        """hmac.compare_digest is called to prevent timing attacks."""
        secret = "s"
        data = b'{"x":1}'
        sig = _make_signature(data, secret)

        with patch("hmac.compare_digest", wraps=hmac.compare_digest) as mock_cd:
            verify_signature(data, sig, secret)

        mock_cd.assert_called_once()

    def test_signature_sensitive_to_whitespace(self):
        """Even a single extra space in data makes the signature invalid."""
        secret = "s"
        data_exact = b'{"parcelId":"1"}'
        data_padded = b'{ "parcelId":"1"}'
        sig = _make_signature(data_exact, secret)

        assert verify_signature(data_exact, sig, secret) is True
        assert verify_signature(data_padded, sig, secret) is False


# ---------------------------------------------------------------------------
# validate_envelope
# ---------------------------------------------------------------------------


class TestValidateEnvelope:
    def _valid_envelope(self):
        return {
            "specversion": "1.0",
            "type": "gr.boxnow.parcel_event_change",
            "source": "boxnow",
            "subject": "9219709201",
            "id": "msg-1",
            "time": "2025-01-01T12:00:00Z",
            "datacontenttype": "application/json",
            "datasignature": "abc",
            "data": {},
        }

    def test_valid_envelope_passes(self):
        """A conforming envelope does not raise."""
        validate_envelope(self._valid_envelope())  # must not raise

    def test_wrong_specversion_raises(self):
        """specversion != '1.0' raises BoxNowWebhookError."""
        env = self._valid_envelope()
        env["specversion"] = "2.0"
        with pytest.raises(BoxNowWebhookError, match="specversion"):
            validate_envelope(env)

    def test_wrong_type_raises(self):
        """type != 'gr.boxnow.parcel_event_change' raises BoxNowWebhookError."""
        env = self._valid_envelope()
        env["type"] = "com.example.other"
        with pytest.raises(BoxNowWebhookError, match="type"):
            validate_envelope(env)

    def test_missing_specversion_raises(self):
        """Missing specversion key raises BoxNowWebhookError."""
        env = self._valid_envelope()
        del env["specversion"]
        with pytest.raises(BoxNowWebhookError, match="specversion"):
            validate_envelope(env)
