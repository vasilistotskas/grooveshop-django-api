"""Canonical resolution of the real client IP behind Cloudflare.

The infrastructure side is ``docs/edge-trust-boundary.md`` in
grooveshop-infrastructure; this module is the decision it leaves to us.

**What Traefik can and cannot tell us.** The address Traefik saw is its
TCP peer, and it appends that as the rightmost ``X-Forwarded-For`` hop
and sets ``X-Real-IP`` to it. For proxied traffic the peer is a
Cloudflare edge node, not the visitor — and until the Traefik Service
moved to ``externalTrafficPolicy: Local`` it was a ``10.42.x.x`` SNAT
gateway (a tagged public request was recorded as ``10.42.1.0`` on
2026-09-16). Either way, keying anonymous throttles on it makes budgets
meant to be *per caller* shared by everyone behind that address:
``order_create_anon`` at 10/minute let one client lock every guest out
of checkout.

**Why the edge headers cannot simply be trusted.** ``CF-Connecting-IP``
is set by Cloudflare, but the origin also answers directly on its node
IPs, so any caller can send one and mint a fresh throttle bucket per
request — an unlimited-enumeration hole on endpoints like
``gift_card_check``, whose whole purpose is to stop code guessing. Even a
request that really came from a Cloudflare address proves only
"Cloudflare", not "our zone": a Worker in anyone's zone can choose the
value (Cloudflare docs, "CF-Connecting-IP in Worker subrequests").

**The trust boundary.** A Cloudflare Transform Rule in each of OUR zones
stamps every request with ``X-Origin-Verify: <shared secret>``. Present
and correct means the request came through our zone, so its Cloudflare
IP header is Cloudflare's; absent or wrong means fall back.

It fails SAFE: if the Transform Rule is missing, the secret is unset, or
the two drift apart, ``trusted_client_ip`` returns ``None`` and callers
key on the peer Traefik saw (``client_ip_or_peer``), which is coarse but
cannot be chosen by the caller.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest
from django.utils.crypto import constant_time_compare

# Checked in order. ``CF-Connecting-IP`` comes first because Traefik also
# sets ``X-Real-IP`` — to its TCP peer, the Cloudflare edge — on the direct
# browser -> Cloudflare -> Traefik -> Django path, so preferring X-Real-IP
# there would key every visitor on the edge node. On the SSR path
# (-> Nuxt -> Django) the Nuxt proxy resolves the visitor from
# ``CF-Connecting-IP`` and forwards it as ``X-Real-IP``, which is why the
# fallback is still needed.
_IP_HEADERS = ("HTTP_CF_CONNECTING_IP", "HTTP_X_REAL_IP")


def request_came_through_edge(request: HttpRequest) -> bool:
    """True when this request provably transited our Cloudflare edge."""
    secret = getattr(settings, "ORIGIN_VERIFY_SECRET", "") or ""
    if not secret:
        return False
    presented = request.META.get("HTTP_X_ORIGIN_VERIFY", "") or ""
    if not presented:
        return False
    # Constant-time: this is a secret comparison on an attacker-supplied
    # value, so a short-circuiting == would leak it a byte at a time.
    return constant_time_compare(presented, secret)


def trusted_client_ip(request: HttpRequest) -> str | None:
    """Return the real client IP, or ``None`` if it cannot be trusted.

    ``None`` is a deliberate, meaningful answer: it means "provenance not
    established", and every caller must fall back rather than guess. Do
    NOT add a REMOTE_ADDR fallback here — that is the proxy's address,
    and returning it would silently defeat the whole module. Callers
    that need *some* key use ``client_ip_or_peer``.
    """
    if not request_came_through_edge(request):
        return None
    for header in _IP_HEADERS:
        value = (request.META.get(header) or "").strip()
        if value:
            # A forwarded chain can appear here if an upstream ever
            # concatenates; take the first entry, which Cloudflare sets
            # to the connecting visitor.
            return value.split(",")[0].strip()
    return None


def client_ip_or_peer(request: HttpRequest) -> str:
    """The address to key a per-client limit on: never caller-chosen.

    The edge-proven visitor when there is one, otherwise the peer Traefik
    saw — the rightmost ``X-Forwarded-For`` hop, the same one DRF's
    ``get_ident`` takes with ``NUM_PROXIES = 1``. Traefik deletes the
    header from every peer outside Cloudflare's ranges and appends its
    own peer after that, and the Nuxt proxy relays the header unchanged,
    so that hop is Traefik's on both the direct and the SSR path.
    ``REMOTE_ADDR`` last, for callers that reach Django without Traefik
    (probes, tests, in-cluster services).

    Never ``X-Real-IP`` without the proof: on the SSR path Nuxt fills it
    from the visitor's own ``CF-Connecting-IP``, which a caller hitting a
    node IP can set to anything.
    """
    edge_ip = trusted_client_ip(request)
    if edge_ip:
        return edge_ip
    hops = [
        hop.strip()
        for hop in (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")
        if hop.strip()
    ]
    if hops:
        return hops[-1]
    return request.META.get("REMOTE_ADDR", "") or ""
