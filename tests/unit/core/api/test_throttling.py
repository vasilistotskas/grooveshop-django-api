"""Tests for the gateway-aware anonymous cart-mutation throttle.

``CartMutationAnonThrottle`` keys on the cart UUID instead of the
client IP when — and only when — the request authenticates itself as
the agent gateway via the ``X-Internal-Gateway`` shared secret. All
agent traffic egresses from gateway pods, so IP keying would collapse
every AI agent into one shared bucket.
"""

from __future__ import annotations

from unittest import mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from cart.agent_token import hash_cart_token
from core.api.throttling import (
    CartMutationAnonThrottle,
    CartMutationThrottle,
    OrderCreateAnonThrottle,
    OrderCreateThrottle,
    PaymentAttemptAnonThrottle,
    PaymentAttemptThrottle,
    SearchThrottle,
)
from tests.utils.redis import private_cache

SECRET = "gateway-shared-secret"
CART_UUID = "521b14f2-da94-48eb-9426-9cfa922606f8"


def _anon_request(factory: APIRequestFactory, **headers):
    request = factory.post("/api/v1/cart/item", headers=headers)
    request.user = AnonymousUser()
    return request


class _GatewayCartKeyTests:
    """A request the agent gateway authenticates is keyed on its cart.

    Every buyer an AI platform serves reaches Django from the platform's
    address; keyed on it, they would all share one budget.
    """

    throttle_class: type

    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.throttle = self.throttle_class()

    def test_a_signed_in_gateway_request_is_not_an_anonymous_one(self) -> None:
        """An agent linked to an account is throttled by the user budget;
        the anonymous one does not count it too."""
        request = self.factory.post(
            "/api/v1/cart/item",
            headers={"X-Internal-Gateway": SECRET, "X-Cart-Id": CART_UUID},
        )
        request.user = type(
            "SignedIn", (), {"is_authenticated": True, "pk": 7}
        )()
        self.assertIsNone(self.throttle.get_cache_key(request, view=None))

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

    def test_gateway_cart_token_keys_on_its_hash_never_the_token(self) -> None:
        token = "a-cart-token-value"
        key = self.throttle.get_cache_key(
            _anon_request(
                self.factory,
                **{"X-Internal-Gateway": SECRET, "X-Cart-Token": token},
            ),
            view=None,
        )
        self.assertIn(f"gw:{hash_cart_token(token)}", key)
        self.assertNotIn(token, key)

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


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET)
class CartMutationAnonThrottleTestCase(_GatewayCartKeyTests, TestCase):
    throttle_class = CartMutationAnonThrottle


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET)
class OrderCreateAnonThrottleTestCase(_GatewayCartKeyTests, TestCase):
    """Order creation from the gateway is budgeted per cart (#699)."""

    throttle_class = OrderCreateAnonThrottle


EDGE_SECRET = "edge-shared-secret"


@override_settings(ORIGIN_VERIFY_SECRET=EDGE_SECRET)
class UserOrIpRateThrottleIdentityTestCase(TestCase):
    """The bucket must be per visitor, not per cluster.

    Behind k3s ServiceLB every request is SNATd to the node gateway, so
    keying on the rightmost X-Forwarded-For hop put every anonymous
    visitor in ONE bucket — a store-wide budget masquerading as a
    per-caller one. These assert the two halves of the fix.
    """

    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.throttle = SearchThrottle()

    def _key(self, **headers):
        request = self.factory.get("/api/v1/search/product", headers=headers)
        request.user = AnonymousUser()
        return self.throttle.get_cache_key(request, view=None)

    def test_two_visitors_behind_the_edge_get_different_buckets(self):
        first = self._key(
            **{
                "x-origin-verify": EDGE_SECRET,
                "cf-connecting-ip": "203.0.113.9",
            }
        )
        second = self._key(
            **{
                "x-origin-verify": EDGE_SECRET,
                "cf-connecting-ip": "198.51.100.77",
            }
        )
        assert first != second
        assert "203.0.113.9" in first
        assert "198.51.100.77" in second

    def test_unproven_requests_share_the_coarse_fallback(self):
        """Without proof of edge the value is forgeable, so it is refused
        and the old un-spoofable identity is used instead."""
        forged = self._key(**{"cf-connecting-ip": "203.0.113.9"})
        assert "203.0.113.9" not in forged


@pytest.mark.parametrize(
    "throttle_class",
    [
        PaymentAttemptAnonThrottle,
        OrderCreateAnonThrottle,
        CartMutationAnonThrottle,
    ],
)
@override_settings(ORIGIN_VERIFY_SECRET=EDGE_SECRET)
def test_anon_halves_key_on_the_proven_visitor(throttle_class):
    """Guests paying or checking out through the same Cloudflare edge
    node reach Django from one address. Keyed on it, one guest's burst
    would lock every other guest out of payment and checkout."""
    factory = APIRequestFactory()
    throttle = throttle_class()

    def key(client_ip):
        request = factory.post(
            "/api/v1/order",
            headers={
                "x-origin-verify": EDGE_SECRET,
                "cf-connecting-ip": client_ip,
                "x-forwarded-for": "172.70.1.1",
            },
        )
        request.user = AnonymousUser()
        return throttle.get_cache_key(request, view=None)

    first, second = key("203.0.113.9"), key("198.51.100.77")

    assert first != second
    assert "203.0.113.9" in first
    assert "198.51.100.77" in second


@pytest.mark.parametrize(
    "throttle_class", [SearchThrottle, OrderCreateAnonThrottle]
)
def test_an_unproven_request_keys_on_the_peer_rule_of_client_ip(
    throttle_class,
):
    """Without the edge proof the bucket is ``client_ip_or_peer``'s peer.

    A last ``X-Forwarded-For`` hop that is not an address is never a key:
    DRF's ``get_ident`` took it as it stood.
    """
    request = APIRequestFactory().post(
        "/api/v1/order",
        headers={"x-forwarded-for": "198.51.100.7, not-an-address"},
        REMOTE_ADDR="10.42.0.9",
    )
    request.user = AnonymousUser()

    key = throttle_class().get_cache_key(request, view=None)

    assert "not-an-address" not in key
    assert key.endswith("10.42.0.9")


ORDER_UUID = "7f9c2b1e-3a4d-4e5f-8a6b-1c2d3e4f5a6b"


def _payment_request(*, order_uuid=ORDER_UUID, **headers):
    request = APIRequestFactory().post(
        f"/api/v1/order/1/create_checkout_session?uuid={order_uuid}",
        headers=headers,
    )
    request.user = AnonymousUser()
    return PaymentAttemptAnonThrottle().get_cache_key(request, view=None)


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET)
class PaymentAttemptAnonThrottleTestCase(TestCase):
    """The gateway asks for a payment link right after placing an online
    order; keyed on the platform's address, the sixth agent order in a
    minute would be placed with no link. Keyed on the order instead."""

    def test_a_gateway_request_keys_on_the_orders_hash(self) -> None:
        key = _payment_request(**{"X-Internal-Gateway": SECRET})
        self.assertIn("gw:order:", key)
        # The uuid opens a guest order: never kept in a cache key.
        self.assertNotIn(ORDER_UUID, key)

    def test_two_orders_get_distinct_buckets(self) -> None:
        other = "0e35c7e5-8a5d-4f6e-9be1-2be929e14a41"
        self.assertNotEqual(
            _payment_request(**{"X-Internal-Gateway": SECRET}),
            _payment_request(
                order_uuid=other, **{"X-Internal-Gateway": SECRET}
            ),
        )

    def test_a_signed_in_gateway_request_is_not_counted(self) -> None:
        request = APIRequestFactory().post(
            f"/api/v1/order/1/create_checkout_session?uuid={ORDER_UUID}",
            headers={"X-Internal-Gateway": SECRET},
        )
        request.user = type(
            "SignedIn", (), {"is_authenticated": True, "pk": 7}
        )()
        self.assertIsNone(
            PaymentAttemptAnonThrottle().get_cache_key(request, view=None)
        )

    def test_without_the_secret_it_keys_on_the_address(self) -> None:
        key = _payment_request(**{"X-Internal-Gateway": "not-the-secret"})
        self.assertNotIn("gw:", key)
        self.assertNotIn(ORDER_UUID, key)


class _GatewayUserBudgetTests:
    """The signed-in half of a pair (``UserRateThrottle``) keys an
    anonymous request on its address, so every buyer a gateway serves was
    one caller. A request carrying the gateway's secret is keyed on its
    cart or order instead, as the anonymous half is; everyone else keeps
    the address.
    """

    throttle_class: type
    scope: str

    def setUp(self) -> None:
        self.factory = APIRequestFactory()

    def _key(self, *, path="/api/v1/cart/item", user=None, **headers):
        request = self.factory.post(path, headers=headers)
        request.user = user if user is not None else AnonymousUser()
        return self.throttle_class().get_cache_key(request, view=None)

    def _allowed(self, cache, *, rate="1/minute", **headers) -> bool:
        throttle_class = self.throttle_class
        with (
            mock.patch.object(throttle_class, "cache", cache),
            mock.patch.object(throttle_class, "rate", rate, create=True),
        ):
            request = self.factory.post("/api/v1/cart/item", headers=headers)
            request.user = AnonymousUser()
            return throttle_class().allow_request(request, view=None)

    def test_a_gateway_request_keys_on_its_cart(self) -> None:
        key = self._key(
            **{"X-Internal-Gateway": SECRET, "X-Cart-Id": CART_UUID}
        )
        self.assertIn(f"gw:{CART_UUID}", key)
        self.assertIn(self.scope, key)

    def test_a_gateway_cart_token_keys_on_its_hash_never_the_token(
        self,
    ) -> None:
        token = "a-cart-token-value"
        key = self._key(**{"X-Internal-Gateway": SECRET, "X-Cart-Token": token})
        self.assertIn(f"gw:{hash_cart_token(token)}", key)
        self.assertNotIn(token, key)

    def test_two_gateway_carts_have_separate_budgets(self) -> None:
        cache = private_cache()
        other = "0e35c7e5-8a5d-4f6e-9be1-2be929e14a41"
        gateway = {"X-Internal-Gateway": SECRET}
        keys = [
            self._key(**gateway, **{"X-Cart-Id": cart})
            for cart in (CART_UUID, other)
        ]
        try:
            self.assertTrue(
                self._allowed(cache, **gateway, **{"X-Cart-Id": CART_UUID})
            )
            self.assertFalse(
                self._allowed(cache, **gateway, **{"X-Cart-Id": CART_UUID})
            )
            self.assertTrue(
                self._allowed(cache, **gateway, **{"X-Cart-Id": other})
            )
        finally:
            cache.delete_many(keys)

    def test_storefront_guests_still_share_their_address_budget(
        self,
    ) -> None:
        cache = private_cache()
        headers = {"X-Cart-Id": CART_UUID}
        key = self._key(**headers)
        try:
            self.assertNotIn(CART_UUID, key)
            self.assertTrue(self._allowed(cache, **headers))
            # Another cart from the same address is the same guest.
            self.assertFalse(
                self._allowed(
                    cache,
                    **{"X-Cart-Id": "0e35c7e5-8a5d-4f6e-9be1-2be929e14a41"},
                )
            )
        finally:
            cache.delete(key)

    def test_a_forged_gateway_header_without_the_secret_keys_on_the_address(
        self,
    ) -> None:
        for headers in (
            {"X-Internal-Gateway": "not-the-secret", "X-Cart-Id": CART_UUID},
            {"X-Internal-Gateway": SECRET},
            {"X-Cart-Id": CART_UUID},
        ):
            with self.subTest(headers=headers):
                self.assertNotIn(CART_UUID, self._key(**headers))
                self.assertNotIn("gw:", self._key(**headers))

    @override_settings(AGENT_GATEWAY_INTERNAL_SECRET="")
    def test_an_unset_secret_never_matches(self) -> None:
        key = self._key(**{"X-Internal-Gateway": "", "X-Cart-Id": CART_UUID})
        self.assertNotIn("gw:", key)

    def test_a_signed_in_caller_keys_on_the_account(self) -> None:
        user = type("SignedIn", (), {"is_authenticated": True, "pk": 7})()
        key = self._key(
            user=user,
            **{"X-Internal-Gateway": SECRET, "X-Cart-Id": CART_UUID},
        )
        self.assertTrue(key.endswith("_7"))
        self.assertNotIn("gw:", key)


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET)
class CartMutationThrottleTestCase(_GatewayUserBudgetTests, TestCase):
    throttle_class = CartMutationThrottle
    scope = "cart_mutation"


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET)
class OrderCreateThrottleTestCase(_GatewayUserBudgetTests, TestCase):
    throttle_class = OrderCreateThrottle
    scope = "order_create"


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET)
class PaymentAttemptThrottleTestCase(_GatewayUserBudgetTests, TestCase):
    throttle_class = PaymentAttemptThrottle
    scope = "payment"

    def test_a_gateway_request_keys_on_the_orders_hash(self) -> None:
        key = self._key(
            path=f"/api/v1/order/1/create_checkout_session?uuid={ORDER_UUID}",
            **{"X-Internal-Gateway": SECRET},
        )
        self.assertIn("gw:order:", key)
        self.assertNotIn(ORDER_UUID, key)
