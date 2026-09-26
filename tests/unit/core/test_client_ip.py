"""Tests for the Cloudflare edge trust boundary.

These guard a security property, so each one is written to FAIL if the
guard is removed: drop the ``X-Origin-Verify`` check and
``test_forged_cf_header_without_secret_is_ignored`` starts trusting an
attacker-supplied address.
"""

import pytest
from django.test import RequestFactory

from core.client_ip import (
    client_ip_or_peer,
    request_came_through_edge,
    trusted_client_ip,
)
from core.middleware.allauth_ratelimit import _client_key
from core.middleware.idempotency import _get_real_ip
from user.adapter import UserAccountAdapter

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
    """Traefik sets X-Real-IP to its TCP peer (an SNAT gateway, or the
    Cloudflare edge) on the direct path, so preferring it would key every
    visitor on that one address."""
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


# ``client_ip_or_peer`` — the key for limits that must always have one.
# Traefik's view of the peer is the rightmost X-Forwarded-For hop; the
# entries left of it are whatever a Cloudflare peer (or the visitor through
# it) sent.
EDGE_HOP = "172.71.8.4"


def test_or_peer_prefers_the_edge_proven_visitor(rf, settings):
    settings.ORIGIN_VERIFY_SECRET = SECRET
    request = _req(
        rf,
        HTTP_X_ORIGIN_VERIFY=SECRET,
        HTTP_CF_CONNECTING_IP=REAL_IP,
        HTTP_X_FORWARDED_FOR=f"{REAL_IP}, {EDGE_HOP}",
    )
    assert client_ip_or_peer(request) == REAL_IP


def test_or_peer_ignores_forged_headers_without_the_proof(rf, settings):
    """The property: a caller cannot choose its own key."""
    settings.ORIGIN_VERIFY_SECRET = SECRET
    request = _req(
        rf,
        HTTP_CF_CONNECTING_IP="1.2.3.4",
        HTTP_X_REAL_IP="1.2.3.4",
        HTTP_X_FORWARDED_FOR=f"1.2.3.4, {EDGE_HOP}",
    )
    assert client_ip_or_peer(request) == EDGE_HOP


def test_or_peer_falls_back_to_remote_addr_without_traefik(rf, settings):
    settings.ORIGIN_VERIFY_SECRET = SECRET
    request = _req(rf)
    assert client_ip_or_peer(request) == "10.42.1.0"


@pytest.mark.parametrize(
    "key_of",
    [
        pytest.param(_client_key, id="allauth-ratelimit"),
        pytest.param(_get_real_ip, id="idempotency-scope"),
        pytest.param(
            lambda request: UserAccountAdapter().get_client_ip(request),
            id="allauth-adapter",
        ),
    ],
)
def test_limit_keys_do_not_follow_a_forged_x_real_ip(rf, settings, key_of):
    """These used to take X-Real-IP as given. The Nuxt proxy fills it from
    the caller's CF-Connecting-IP, so a caller at a node IP picked a fresh
    login/idempotency bucket per request."""
    settings.ORIGIN_VERIFY_SECRET = SECRET
    first = _req(rf, HTTP_X_REAL_IP="1.2.3.4", HTTP_X_FORWARDED_FOR=EDGE_HOP)
    second = _req(rf, HTTP_X_REAL_IP="5.6.7.8", HTTP_X_FORWARDED_FOR=EDGE_HOP)
    assert key_of(first) == key_of(second)

    proven = _req(
        rf,
        HTTP_X_ORIGIN_VERIFY=SECRET,
        HTTP_X_REAL_IP=REAL_IP,
        HTTP_X_FORWARDED_FOR=EDGE_HOP,
    )
    assert key_of(proven) != key_of(first)


VIVA_IP = "51.138.37.238"  # in VIVA_WEBHOOK_IPS_PRODUCTION


@pytest.fixture
def viva_live(monkeypatch):
    monkeypatch.setattr(
        "tenant.credentials.viva_wallet_credentials",
        lambda: {"live_mode": True},
    )


def test_viva_source_check_ignores_a_forged_forwarded_entry(
    rf, settings, viva_live
):
    """It used to accept a Viva address from ANY X-Forwarded-For entry,
    and entries left of Traefik's own hop are the caller's to write."""
    from order.views.viva_webhook import _check_source_ip

    settings.ORIGIN_VERIFY_SECRET = SECRET
    forged = _req(rf, HTTP_X_FORWARDED_FOR=f"{VIVA_IP}, {EDGE_HOP}")
    assert _check_source_ip(forged) == (False, EDGE_HOP)

    proven = _req(
        rf,
        HTTP_X_ORIGIN_VERIFY=SECRET,
        HTTP_CF_CONNECTING_IP=VIVA_IP,
        HTTP_X_FORWARDED_FOR=f"{VIVA_IP}, {EDGE_HOP}",
    )
    assert _check_source_ip(proven) == (True, VIVA_IP)


def test_viva_handshake_budget_does_not_follow_a_forged_x_real_ip(rf, settings):
    from django.core.cache import cache

    from order.views.viva_webhook import _webhook_get_rate_limit

    settings.ORIGIN_VERIFY_SECRET = SECRET
    cache.clear()
    results = [
        _webhook_get_rate_limit(
            _req(rf, HTTP_X_REAL_IP=f"1.2.3.{n}", HTTP_X_FORWARDED_FOR=EDGE_HOP)
        )
        for n in range(11)
    ]
    assert results == [False] * 10 + [True]
