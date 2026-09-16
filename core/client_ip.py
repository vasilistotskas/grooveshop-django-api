"""Canonical resolution of the real client IP behind Cloudflare.

**The problem this exists to solve.** The Traefik Service is
``type: LoadBalancer`` with ``externalTrafficPolicy: Cluster``, so k3s
klipper-lb SNATs every inbound connection to the node's Flannel gateway
before Traefik ever sees it. Traefik then writes that internal address
into ``X-Forwarded-For`` and ``X-Real-IP``. Taking the rightmost XFF hop
— the correct choice behind a single trusted proxy — therefore yields
``10.42.x.x`` for real external traffic, which was proven in production
on 2026-09-16: a tagged request from the public internet was recorded
as ``10.42.1.0``.

The consequence is not cosmetic. Every anonymous scoped throttle built
on ``UserOrIpRateThrottle`` keys on that near-constant internal address,
so budgets meant to be *per caller* are shared by the entire store —
``order_create_anon`` at 10/minute means one client can lock every guest
out of checkout.

**Why the edge headers cannot simply be trusted.** ``CF-Connecting-IP``
is set by Cloudflare and is genuine for proxied traffic, but the origin
also answers directly on its node IPs, so any caller can forge it and
mint a fresh throttle bucket per request. That would turn a
denial-of-service into an unlimited-enumeration hole on endpoints like
``gift_card_check``, whose whole purpose is to stop code guessing.

**The trust boundary.** A Cloudflare Transform Rule stamps every request
that passes through the edge with ``X-Origin-Verify: <shared secret>``.
A caller reaching the origin directly does not know that secret, so the
header is either present-and-correct (the request provably came through
our edge, and its Cloudflare IP headers are therefore genuine) or it is
absent/wrong (fall back to the old, un-spoofable-but-coarse behaviour).

This fails SAFE by construction: if the Transform Rule is missing, the
secret is unset, or the two ever drift apart, every caller simply
resolves to ``None`` here and the callers keep their previous behaviour.
Nothing breaks, throttling merely stays as coarse as it is today.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest
from django.utils.crypto import constant_time_compare

# Checked in order. ``CF-Connecting-IP`` comes first because Traefik also
# sets ``X-Real-IP`` — to the SNAT'd internal address — on the direct
# browser -> Cloudflare -> Traefik -> Django path, so preferring X-Real-IP
# there would reintroduce the exact bug this module exists to fix. On the
# SSR path (-> Nuxt -> Django) the Nuxt proxy resolves the visitor from
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
    NOT add a REMOTE_ADDR fallback here — that is the internal SNAT
    address and returning it would silently defeat the whole module.
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
