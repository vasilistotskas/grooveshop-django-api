"""``_meta_context_from``: the order's Meta context never carries an address
the caller chose, and a checkout without context stays without one."""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from order.views.order import _meta_context_from

SECRET = "s3cret-edge-token"
VISITOR_IP = "203.0.113.9"
FORGED_IP = "198.51.100.77"


@pytest.fixture
def edge_request(settings):
    settings.ORIGIN_VERIFY_SECRET = SECRET
    return RequestFactory().post(
        "/",
        HTTP_X_ORIGIN_VERIFY=SECRET,
        HTTP_CF_CONNECTING_IP=VISITOR_IP,
    )


@pytest.mark.parametrize("raw", [None, {}])
def test_no_context_stays_empty(edge_request, raw):
    assert _meta_context_from(edge_request, raw) == {}


def test_a_body_with_only_an_address_stays_empty(edge_request):
    assert (
        _meta_context_from(edge_request, {"client_ip_address": FORGED_IP}) == {}
    )


def test_the_edge_address_replaces_the_body_address(edge_request):
    context = _meta_context_from(
        edge_request, {"fbp": "fb.1", "client_ip_address": FORGED_IP}
    )

    assert context == {"fbp": "fb.1", "client_ip_address": VISITOR_IP}


def test_without_the_proof_no_address_is_kept(settings):
    settings.ORIGIN_VERIFY_SECRET = SECRET
    request = RequestFactory().post("/", HTTP_CF_CONNECTING_IP=FORGED_IP)

    assert _meta_context_from(
        request, {"fbp": "fb.1", "client_ip_address": FORGED_IP}
    ) == {"fbp": "fb.1"}
