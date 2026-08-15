"""Tests for the gateway-aware anonymous cart-mutation throttle.

``CartMutationAnonThrottle`` keys on the cart UUID instead of the
client IP when — and only when — the request authenticates itself as
the agent gateway via the ``X-Internal-Gateway`` shared secret. All
agent traffic egresses from gateway pods, so IP keying would collapse
every AI agent into one shared bucket.
"""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from core.api.throttling import CartMutationAnonThrottle

SECRET = "gateway-shared-secret"
CART_UUID = "521b14f2-da94-48eb-9426-9cfa922606f8"


def _anon_request(factory: APIRequestFactory, **headers):
    request = factory.post("/api/v1/cart/item", headers=headers)
    request.user = AnonymousUser()
    return request


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET)
class CartMutationAnonThrottleTestCase(TestCase):
    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.throttle = CartMutationAnonThrottle()

    def test_gateway_request_keys_on_cart_uuid(self) -> None:
        request = _anon_request(
            self.factory,
            **{"X-Internal-Gateway": SECRET, "X-Cart-Id": CART_UUID},
        )
        key = self.throttle.get_cache_key(request, view=None)
        self.assertIn(f"gw:{CART_UUID}", key)

    def test_two_carts_get_distinct_buckets(self) -> None:
        other_uuid = "0e35c7e5-8a5d-4f6e-9be1-2be929e14a41"
        key_a = self.throttle.get_cache_key(
            _anon_request(
                self.factory,
                **{"X-Internal-Gateway": SECRET, "X-Cart-Id": CART_UUID},
            ),
            view=None,
        )
        key_b = self.throttle.get_cache_key(
            _anon_request(
                self.factory,
                **{"X-Internal-Gateway": SECRET, "X-Cart-Id": other_uuid},
            ),
            view=None,
        )
        self.assertNotEqual(key_a, key_b)

    def test_wrong_secret_falls_back_to_ip(self) -> None:
        request = _anon_request(
            self.factory,
            **{"X-Internal-Gateway": "not-the-secret", "X-Cart-Id": CART_UUID},
        )
        key = self.throttle.get_cache_key(request, view=None)
        self.assertNotIn(CART_UUID, key)

    def test_missing_cart_id_falls_back_to_ip(self) -> None:
        request = _anon_request(self.factory, **{"X-Internal-Gateway": SECRET})
        key = self.throttle.get_cache_key(request, view=None)
        self.assertNotIn("gw:", key)

    def test_plain_anonymous_request_keys_on_ip(self) -> None:
        request = _anon_request(self.factory, **{"X-Cart-Id": CART_UUID})
        key = self.throttle.get_cache_key(request, view=None)
        self.assertNotIn(CART_UUID, key)

    @override_settings(AGENT_GATEWAY_INTERNAL_SECRET="")
    def test_unset_secret_never_matches(self) -> None:
        # An empty configured secret must not let an empty (or any)
        # header value through — the surface is simply off.
        request = _anon_request(
            self.factory,
            **{"X-Internal-Gateway": "", "X-Cart-Id": CART_UUID},
        )
        key = self.throttle.get_cache_key(request, view=None)
        self.assertNotIn(CART_UUID, key)
