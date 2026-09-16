"""Tests for the Cloudflare edge trust boundary.

These guard a security property, so each one is written to FAIL if the
guard is removed: drop the ``X-Origin-Verify`` check and
``test_forged_cf_header_without_secret_is_ignored`` starts trusting an
attacker-supplied address.
"""

import pytest
from django.test import RequestFactory

from core.client_ip import request_came_through_edge, trusted_client_ip

SECRET = "s3cret-edge-token"
REAL_IP = "203.0.113.9"


@pytest.fixture
def rf():
    return RequestFactory()


def _req(rf, **headers):
    # RequestFactory puts the SNAT'd proxy address in REMOTE_ADDR, which
    # is what production actually looks like behind klipper-lb.
    return rf.get("/api/v1/product", REMOTE_ADDR="10.42.1.0", **headers)


def test_trusts_cf_connecting_ip_when_secret_matches(rf, settings):
    settings.ORIGIN_VERIFY_SECRET = SECRET
    request = _req(
        rf, HTTP_X_ORIGIN_VERIFY=SECRET, HTTP_CF_CONNECTING_IP=REAL_IP
    )
    assert trusted_client_ip(request) == REAL_IP


def test_forged_cf_header_without_secret_is_ignored(rf, settings):
    """The core security property: no secret, no trust."""
    settings.ORIGIN_VERIFY_SECRET = SECRET
    request = _req(rf, HTTP_CF_CONNECTING_IP="1.2.3.4")
    assert trusted_client_ip(request) is None


def test_wrong_secret_is_ignored(rf, settings):
    settings.ORIGIN_VERIFY_SECRET = SECRET
    request = _req(
        rf, HTTP_X_ORIGIN_VERIFY="not-the-secret", HTTP_CF_CONNECTING_IP=REAL_IP
    )
    assert trusted_client_ip(request) is None


def test_unconfigured_secret_never_trusts(rf, settings):
    """Fail-safe: an unset secret must not make everything trusted."""
    settings.ORIGIN_VERIFY_SECRET = ""
    request = _req(rf, HTTP_X_ORIGIN_VERIFY="", HTTP_CF_CONNECTING_IP=REAL_IP)
    assert trusted_client_ip(request) is None
    assert request_came_through_edge(request) is False


def test_falls_back_to_x_real_ip_for_the_ssr_path(rf, settings):
    """Nuxt resolves the visitor and forwards it as X-Real-IP."""
    settings.ORIGIN_VERIFY_SECRET = SECRET
    request = _req(rf, HTTP_X_ORIGIN_VERIFY=SECRET, HTTP_X_REAL_IP=REAL_IP)
    assert trusted_client_ip(request) == REAL_IP


def test_cf_connecting_ip_wins_over_x_real_ip(rf, settings):
    """Traefik sets X-Real-IP to the SNAT address on the direct path, so
    preferring it would reintroduce the bug."""
    settings.ORIGIN_VERIFY_SECRET = SECRET
    request = _req(
        rf,
        HTTP_X_ORIGIN_VERIFY=SECRET,
        HTTP_CF_CONNECTING_IP=REAL_IP,
        HTTP_X_REAL_IP="10.42.1.0",
    )
    assert trusted_client_ip(request) == REAL_IP


def test_never_returns_the_internal_remote_addr(rf, settings):
    """None means 'unproven' — it must never degrade to the SNAT address,
    which is what made every visitor share one throttle bucket."""
    settings.ORIGIN_VERIFY_SECRET = SECRET
    request = _req(rf, HTTP_X_ORIGIN_VERIFY=SECRET)
    assert trusted_client_ip(request) is None


def test_chained_value_takes_the_connecting_visitor(rf, settings):
    settings.ORIGIN_VERIFY_SECRET = SECRET
    request = _req(
        rf,
        HTTP_X_ORIGIN_VERIFY=SECRET,
        HTTP_CF_CONNECTING_IP=f"{REAL_IP}, 172.71.8.4",
    )
    assert trusted_client_ip(request) == REAL_IP


def test_non_ascii_secret_does_not_raise(rf, settings):
    """Header bytes arrive latin-1 decoded; a high byte must not 500."""
    settings.ORIGIN_VERIFY_SECRET = SECRET
    request = _req(
        rf, HTTP_X_ORIGIN_VERIFY="ünicode", HTTP_CF_CONNECTING_IP=REAL_IP
    )
    assert trusted_client_ip(request) is None
