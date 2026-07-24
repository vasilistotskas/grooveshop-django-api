"""Tests for the in-flight reservation in IdempotencyMiddleware (G0103)."""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from django.http import JsonResponse
from django.test import RequestFactory, TestCase, override_settings

from core.middleware.idempotency import (
    IDEMPOTENCY_HEADER,
    IdempotencyMiddleware,
)

_LOCMEM = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "idempotency-test",
    }
}


@override_settings(CACHES=_LOCMEM)
class IdempotencyInFlightTest(TestCase):
    def _request(self):
        req = RequestFactory().post("/api/v1/order/")
        req.META[IDEMPOTENCY_HEADER] = "key-123"
        req.user = AnonymousUser()
        req.session = None
        return req

    def test_concurrent_duplicate_gets_409_while_in_flight(self):
        from django.core.cache import caches

        caches["default"].clear()
        mw = IdempotencyMiddleware(lambda r: JsonResponse({"ok": True}))

        # First request reserves the key and proceeds to the handler.
        first = self._request()
        self.assertIsNone(mw.process_request(first))

        # A duplicate arriving BEFORE the first completes is rejected with
        # 409 rather than executing the handler a second time.
        second = self._request()
        resp = mw.process_request(second)
        self.assertIsNotNone(resp)
        self.assertEqual(resp.status_code, 409)

    def test_reservation_released_on_non_cacheable_response(self):
        from django.core.cache import caches

        caches["default"].clear()
        mw = IdempotencyMiddleware(lambda r: JsonResponse({"ok": True}))

        first = self._request()
        mw.process_request(first)
        # A 500 (retryable) must release the reservation, not cache it.
        mw.process_response(first, JsonResponse({"err": 1}, status=500))

        # A retry after the failure is allowed to proceed (not 409-blocked).
        retry = self._request()
        self.assertIsNone(mw.process_request(retry))
