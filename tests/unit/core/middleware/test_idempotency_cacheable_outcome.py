"""Which responses ``IdempotencyMiddleware`` caches, and how it replays them.

Covers ``core/middleware/idempotency.py``: ``_cacheable_outcome`` (redirect,
oversized, non-JSON, undecodable and list bodies, preserved headers), the
replay path in ``process_request`` and the over-long key guard.
"""

from __future__ import annotations

import json

from django.contrib.auth.models import AnonymousUser
from django.core.cache import caches
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)

from core.middleware.idempotency import (
    CACHE_ALIAS,
    IDEMPOTENCY_HEADER,
    MAX_CACHED_BODY_BYTES,
    MAX_KEY_LENGTH,
    IdempotencyMiddleware,
    _cache_key,
)

_LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "idempotency-cacheable-outcome-test",
    }
}


def _request(method: str = "POST", key: str = "key-abc"):
    req = getattr(RequestFactory(), method.lower())("/api/v1/order/")
    req.META[IDEMPOTENCY_HEADER] = key
    req.user = AnonymousUser()
    req.session = None
    return req


class CacheableOutcomeTest(SimpleTestCase):
    def test_redirect_is_not_cacheable(self):
        response = HttpResponseRedirect("/elsewhere/")

        assert IdempotencyMiddleware._cacheable_outcome(response) is None

    def test_body_over_the_size_cap_is_not_cacheable(self):
        response = HttpResponse(
            b'"' + b"x" * MAX_CACHED_BODY_BYTES + b'"',
            content_type="application/json",
        )

        assert IdempotencyMiddleware._cacheable_outcome(response) is None

    def test_body_exactly_at_the_size_cap_is_cacheable(self):
        # Two quote bytes plus padding: a JSON string of exactly the cap.
        payload = b'"' + b"x" * (MAX_CACHED_BODY_BYTES - 2) + b'"'
        response = HttpResponse(payload, content_type="application/json")

        outcome = IdempotencyMiddleware._cacheable_outcome(response)

        assert outcome is not None
        assert outcome["body"] == "x" * (MAX_CACHED_BODY_BYTES - 2)

    def test_non_json_content_type_is_not_cacheable(self):
        response = HttpResponse(b"<p>ok</p>", content_type="text/html")

        assert IdempotencyMiddleware._cacheable_outcome(response) is None

    def test_empty_body_is_not_cacheable(self):
        response = HttpResponse(
            b"", status=204, content_type="application/json"
        )

        assert IdempotencyMiddleware._cacheable_outcome(response) is None

    def test_json_content_type_with_invalid_json_is_not_cacheable(self):
        response = HttpResponse(b"{not json", content_type="application/json")

        assert IdempotencyMiddleware._cacheable_outcome(response) is None

    def test_json_content_type_with_undecodable_bytes_is_not_cacheable(self):
        response = HttpResponse(b"\xff\xfe", content_type="application/json")

        assert IdempotencyMiddleware._cacheable_outcome(response) is None

    def test_list_body_is_cached_verbatim(self):
        response = JsonResponse([{"id": 1}, {"id": 2}], safe=False)

        outcome = IdempotencyMiddleware._cacheable_outcome(response)

        assert outcome == {
            "status": 200,
            "body": [{"id": 1}, {"id": 2}],
            "headers": {"Content-Type": "application/json"},
        }

    def test_client_error_is_cached_with_its_status(self):
        response = JsonResponse({"detail": "Out of stock."}, status=409)

        outcome = IdempotencyMiddleware._cacheable_outcome(response)

        assert outcome is not None
        assert outcome["status"] == 409
        assert outcome["body"] == {"detail": "Out of stock."}

    def test_only_allowlisted_headers_are_preserved(self):
        response = JsonResponse({"id": 7}, status=201)
        response["Location"] = "/api/v1/order/7/"
        response["X-Correlation-ID"] = "corr-1"
        response["Vary"] = "Accept-Language"
        response["Set-Cookie"] = "sessionid=secret"
        response["X-Custom"] = "dropped"

        outcome = IdempotencyMiddleware._cacheable_outcome(response)

        assert outcome is not None
        assert outcome["headers"] == {
            "Content-Type": "application/json",
            "Location": "/api/v1/order/7/",
            "X-Correlation-ID": "corr-1",
            "Vary": "Accept-Language",
        }


@override_settings(CACHES=_LOCMEM)
class IdempotencyReplayTest(TestCase):
    def setUp(self):
        caches[CACHE_ALIAS].clear()
        self.mw = IdempotencyMiddleware(lambda r: JsonResponse({"ok": True}))

    def test_non_cacheable_redirect_releases_the_reservation(self):
        first = _request()
        assert self.mw.process_request(first) is None

        self.mw.process_response(first, HttpResponseRedirect("/next/"))

        assert caches[CACHE_ALIAS].get(_cache_key(first, "key-abc")) is None
        assert self.mw.process_request(_request()) is None

    def test_replay_returns_cached_list_body_status_and_headers(self):
        first = _request()
        self.mw.process_request(first)
        original = JsonResponse([{"id": 1}], safe=False, status=201)
        original["Location"] = "/api/v1/order/1/"
        original["X-Custom"] = "not-replayed"
        self.mw.process_response(first, original)

        replay = self.mw.process_request(_request())

        assert replay is not None
        assert replay.status_code == 201
        assert json.loads(replay.content) == [{"id": 1}]
        assert replay["Location"] == "/api/v1/order/1/"
        assert replay["Idempotent-Replay"] == "true"
        assert not replay.has_header("X-Custom")

    def test_replay_of_a_cached_client_error_keeps_the_error(self):
        first = _request()
        self.mw.process_request(first)
        self.mw.process_response(
            first, JsonResponse({"detail": "Invalid coupon."}, status=400)
        )

        replay = self.mw.process_request(_request())

        assert replay is not None
        assert replay.status_code == 400
        assert json.loads(replay.content) == {"detail": "Invalid coupon."}

    def test_same_key_on_a_different_method_is_not_replayed(self):
        first = _request("POST")
        self.mw.process_request(first)
        self.mw.process_response(first, JsonResponse({"id": 1}, status=201))

        assert self.mw.process_request(_request("PUT")) is None

    def test_safe_methods_are_ignored(self):
        request = _request("GET")

        assert self.mw.process_request(request) is None
        assert not hasattr(request, "_idempotency_cache_key")

    def test_key_longer_than_the_limit_is_rejected_with_400(self):
        response = self.mw.process_request(
            _request(key="k" * (MAX_KEY_LENGTH + 1))
        )

        assert response is not None
        assert response.status_code == 400
        assert json.loads(response.content) == {
            "detail": (
                f"Idempotency-Key must be at most {MAX_KEY_LENGTH} characters."
            )
        }

    def test_key_at_the_limit_is_accepted(self):
        request = _request(key="k" * MAX_KEY_LENGTH)

        assert self.mw.process_request(request) is None
        assert request._idempotency_cache_key == _cache_key(
            request, "k" * MAX_KEY_LENGTH
        )
