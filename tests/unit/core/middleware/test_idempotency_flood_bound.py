"""The idempotency cache must not be an open 24-hour Redis write primitive.

The client chooses `Idempotency-Key`, so a fresh one on every request
minted a new entry — holding up to `MAX_CACHED_BODY_BYTES` for 24 hours
— with nothing bounding the count. This runs at the *middleware* layer,
before DRF reaches a throttle, so no per-endpoint budget could see it.

The deployed Redis is 614 MB on `allkeys-lru`, shared with sessions,
carts, WebSocket tickets and the throttle counters themselves. Filling
it does not fail the flood: it evicts everything else.

Real usage is small — the storefront mints one UUID per checkout attempt
and reuses it across retries, and the agent gateway consumes the header
itself rather than forwarding it — so a per-scope cap a hundred times
above normal use is still a bound.
"""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from django.core.cache import caches
from django.http import JsonResponse
from django.test import RequestFactory, TestCase, override_settings

from core.middleware.idempotency import (
    IDEMPOTENCY_HEADER,
    MAX_KEY_LENGTH,
    MAX_KEYS_PER_SCOPE,
    IdempotencyMiddleware,
)

_LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "idempotency-flood-test",
    }
}


@override_settings(CACHES=_LOCMEM)
class IdempotencyFloodBoundTest(TestCase):
    def setUp(self):
        caches["default"].clear()
        self.mw = IdempotencyMiddleware(lambda r: JsonResponse({"ok": True}))

    def _request(self, key, *, ip="203.0.113.5"):
        req = RequestFactory().post("/api/v1/order/")
        req.META[IDEMPOTENCY_HEADER] = key
        req.META["REMOTE_ADDR"] = ip
        req.user = AnonymousUser()
        req.session = None
        return req

    def _drive(self, key, **kwargs):
        req = self._request(key, **kwargs)
        early = self.mw.process_request(req)
        if early is not None:
            return early
        return self.mw.process_response(req, JsonResponse({"ok": True}))

    def _is_cached(self, key, **kwargs):
        """Did the middleware actually store an entry for this key?"""
        response = self.mw.process_request(self._request(key, **kwargs))
        return (
            response is not None and response.get("Idempotent-Replay") == "true"
        )

    def test_one_scope_stops_minting_entries_at_the_cap(self):
        """Asserted through the middleware, not through the backend.

        An earlier version read locmem's internal dict, which is not
        there when another test in the run leaves the Redis backend
        bound — and "was it stored" is a question the middleware itself
        answers, by replaying or not.
        """
        for n in range(MAX_KEYS_PER_SCOPE + 50):
            self._drive(f"key-{n}")

        assert self._is_cached("key-0"), (
            "a key from within the budget must still replay"
        )
        assert not self._is_cached(f"key-{MAX_KEYS_PER_SCOPE + 40}"), (
            "a key minted past the cap was stored anyway — the scope can "
            "still fill the cache without end"
        )

    def test_going_over_budget_does_not_break_the_request(self):
        """Skipping, not refusing: a flood bound must not become a DoS."""
        for n in range(MAX_KEYS_PER_SCOPE + 5):
            response = self._drive(f"key-{n}")
            assert response.status_code == 200, (
                f"request {n} was refused with {response.status_code}"
            )

    def test_a_retry_of_the_same_key_does_not_spend_budget(self):
        """Only a NEW key costs; that is what the header is for."""
        for _ in range(MAX_KEYS_PER_SCOPE + 20):
            self._drive("the-same-key-every-time")

        replay = self._request("the-same-key-every-time")
        response = self.mw.process_request(replay)
        assert response is not None
        assert response["Idempotent-Replay"] == "true"

    def test_one_scope_cannot_spend_another_scope_budget(self):
        for n in range(MAX_KEYS_PER_SCOPE + 10):
            self._drive(f"key-{n}", ip="203.0.113.5")

        # A different client must still get idempotency protection.
        self._drive("their-own-key", ip="198.51.100.9")
        replay = self._request("their-own-key", ip="198.51.100.9")
        response = self.mw.process_request(replay)

        assert response is not None
        assert response["Idempotent-Replay"] == "true"

    def test_an_absurd_key_is_refused_rather_than_hashed_and_stored(self):
        response = self.mw.process_request(
            self._request("x" * (MAX_KEY_LENGTH + 1))
        )

        assert response is not None
        assert response.status_code == 400

    def test_a_key_at_the_limit_is_accepted(self):
        response = self.mw.process_request(self._request("x" * MAX_KEY_LENGTH))

        assert response is None, "a key at exactly the limit must be allowed"
