"""A malformed secret header must be refused, never crash the request.

`secrets.compare_digest` raises `TypeError` when either `str` argument
holds a non-ASCII character, and Django decodes header bytes with
latin-1 — so any byte >= 0x80 reached the comparison as exactly such a
`str`. The endpoints that use it answer 404 to an anonymous caller
specifically so their existence is not advertised; a 500 on one
particular malformed token advertised it, and filled the error tracker
with what looked like a live incident.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory, override_settings

from core.api.throttling import _gateway_cart_ident
from tenant.internal import is_internal_caller
from tenant.views import _is_gateway

HIGH_BYTE = "\xff"


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET="s3cret")
def test_internal_caller_refuses_a_non_ascii_token():
    assert is_internal_caller(HIGH_BYTE) is False


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET="s3cret")
def test_internal_caller_still_accepts_the_real_secret():
    assert is_internal_caller("s3cret") is True
    assert is_internal_caller("wrong") is False


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET="s3cret")
@pytest.mark.parametrize("token", [HIGH_BYTE, "wrong", ""])
def test_is_gateway_refuses_without_raising(token):
    request = RequestFactory().get("/", HTTP_X_INTERNAL_TOKEN=token)
    assert _is_gateway(request) is False


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET="s3cret")
def test_is_gateway_accepts_the_real_secret():
    request = RequestFactory().get("/", HTTP_X_INTERNAL_TOKEN="s3cret")
    assert _is_gateway(request) is True


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET="s3cret")
def test_throttle_bypass_refuses_a_non_ascii_header():
    request = RequestFactory().get(
        "/",
        HTTP_X_INTERNAL_GATEWAY=HIGH_BYTE,
        HTTP_X_CART_ID="abc",
    )
    assert _gateway_cart_ident(request) is None


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET="s3cret")
def test_throttle_bypass_still_works_for_the_real_secret():
    request = RequestFactory().get(
        "/",
        HTTP_X_INTERNAL_GATEWAY="s3cret",
        HTTP_X_CART_ID="abc",
    )
    assert _gateway_cart_ident(request) == "abc"
