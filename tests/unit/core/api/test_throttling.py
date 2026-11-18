"""Tests for the gateway-aware cart, order and payment throttles.

A throttle keys on a cart (or order) instead of the client IP when, and
only when, the request authenticates itself as the agent gateway via the
``X-Internal-Gateway`` shared secret AND names a cart or order that
exists in this store. All agent traffic egresses from gateway pods, so IP
keying would collapse every AI agent into one shared bucket; a name that
resolves to nothing must not open a budget of its own, or a client making
ids up would never meet a limit.
"""

from __future__ import annotations

import uuid
from unittest import mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory

from cart.agent_token import _cache_key as _token_cache_key
from cart.agent_token import hash_cart_token
from cart.models import Cart
from core.api.throttling import (
    CartMutationAnonThrottle,
    CartMutationThrottle,
    CouponApplyThrottle,
    OrderCreateAnonThrottle,
    OrderCreateThrottle,
    PaymentAttemptAnonThrottle,
    PaymentAttemptThrottle,
    SearchThrottle,
)
from order.factories.order import OrderFactory
from tests.utils.redis import private_cache

SECRET = "gateway-shared-secret"
UNKNOWN_UUID = "0e35c7e5-8a5d-4f6e-9be1-2be929e14a41"


def _guest_cart() -> str:
    """The id of a guest cart that exists in this store."""
    return str(Cart.objects.create(user=None).uuid)


def _spellings(value: str) -> tuple[str, ...]:
    """Every other way the cart lookup (``uuid.UUID``) reads one id."""
    return (
        value.upper(),
        value.replace("-", ""),
        "{" + value + "}",
        "urn:uuid:" + value,
    )


def _anon_request(factory: APIRequestFactory, **headers):
    request = factory.post("/api/v1/cart/item", headers=headers)
    request.user = AnonymousUser()
    return request


class _GatewayCartKeyTests:
    """A request the agent gateway authenticates is keyed on its cart.

    Every buyer an AI platform serves reaches Django from the platform's
    address; keyed on it, they would all share one budget. Only a cart
    that exists earns its own: anything else is the address's.
    """

    throttle_class: type

    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.throttle = self.throttle_class()
        self.cart_id = _guest_cart()

    def _key(self, **headers):
        return self.throttle.get_cache_key(
            _anon_request(self.factory, **headers), view=None
        )

    def test_a_signed_in_gateway_request_is_not_an_anonymous_one(self) -> None:
        """An agent linked to an account is throttled by the user budget;
        the anonymous one does not count it too."""
        request = self.factory.post(
            "/api/v1/cart/item",
            headers={"X-Internal-Gateway": SECRET, "X-Cart-Id": self.cart_id},
        )
        request.user = type(
            "SignedIn", (), {"is_authenticated": True, "pk": 7}
        )()
        self.assertIsNone(self.throttle.get_cache_key(request, view=None))

    def test_gateway_request_keys_on_cart_uuid(self) -> None:
        key = self._key(
            **{"X-Internal-Gateway": SECRET, "X-Cart-Id": self.cart_id}
        )
        self.assertIn(f"gw:{self.cart_id}", key)

    def test_every_spelling_of_a_cart_is_one_budget(self) -> None:
        keys = {
            self._key(**{"X-Internal-Gateway": SECRET, "X-Cart-Id": spelling})
            for spelling in (self.cart_id, *_spellings(self.cart_id))
        }
        self.assertEqual(len(keys), 1)

    def test_a_cart_id_that_is_no_uuid_falls_back_to_ip(self) -> None:
        key = self._key(
            **{"X-Internal-Gateway": SECRET, "X-Cart-Id": "not-a-uuid"}
        )
        self.assertNotIn("not-a-uuid", key)
        self.assertNotIn("gw:", key)

    def test_an_unknown_cart_falls_back_to_ip(self) -> None:
        """The views create a cart when the named one does not resolve, so
        an id that names none must not take a budget of its own."""
        key = self._key(
            **{"X-Internal-Gateway": SECRET, "X-Cart-Id": UNKNOWN_UUID}
        )
        self.assertNotIn(UNKNOWN_UUID, key)
        self.assertNotIn("gw:", key)

    def test_a_signed_in_shoppers_cart_is_not_a_guest_cart(self) -> None:
        """The cart lookup only finds guest carts by id for a caller who is
        not signed in, so another cart's id names nothing."""
        from cart.factories.cart import CartFactory

        owned = str(CartFactory().uuid)
        key = self._key(**{"X-Internal-Gateway": SECRET, "X-Cart-Id": owned})
        self.assertNotIn("gw:", key)

    def test_the_query_uuid_names_no_cart(self) -> None:
        """A cart route has no order: ``?uuid=`` must not take a budget
        from an order that exists."""
        order = OrderFactory(user=None)
        request = _anon_request(self.factory, **{"X-Internal-Gateway": SECRET})
        request.GET = request.GET.copy()
        request.GET["uuid"] = str(order.uuid)
        key = self.throttle.get_cache_key(request, view=None)
        self.assertNotIn("gw:", key)

    def test_two_carts_get_distinct_buckets(self) -> None:
        other = _guest_cart()
        self.assertNotEqual(
            self._key(
                **{"X-Internal-Gateway": SECRET, "X-Cart-Id": self.cart_id}
            ),
            self._key(**{"X-Internal-Gateway": SECRET, "X-Cart-Id": other}),
        )

    def test_a_minted_cart_token_keys_on_its_hash_never_the_token(
        self,
    ) -> None:
        token = "a-cart-token-value"
        cache.set(_token_cache_key(token), {"cart_id": 1}, 60)
        key = self._key(**{"X-Internal-Gateway": SECRET, "X-Cart-Token": token})
        self.assertIn(f"gw:{hash_cart_token(token)}", key)
        self.assertNotIn(token, key)

    def test_a_token_that_was_never_minted_falls_back_to_ip(self) -> None:
        key = self._key(
            **{"X-Internal-Gateway": SECRET, "X-Cart-Token": "made-up"}
        )
        self.assertNotIn("gw:", key)

    def test_wrong_secret_falls_back_to_ip(self) -> None:
        key = self._key(
            **{
                "X-Internal-Gateway": "not-the-secret",
                "X-Cart-Id": self.cart_id,
            }
        )
        self.assertNotIn(self.cart_id, key)

    def test_missing_cart_id_falls_back_to_ip(self) -> None:
        key = self._key(**{"X-Internal-Gateway": SECRET})
        self.assertNotIn("gw:", key)

    def test_plain_anonymous_request_keys_on_ip(self) -> None:
        key = self._key(**{"X-Cart-Id": self.cart_id})
        self.assertNotIn(self.cart_id, key)

    @override_settings(AGENT_GATEWAY_INTERNAL_SECRET="")
    def test_unset_secret_never_matches(self) -> None:
        # An empty configured secret must not let an empty (or any)
        # header value through — the surface is simply off.
        key = self._key(**{"X-Internal-Gateway": "", "X-Cart-Id": self.cart_id})
        self.assertNotIn(self.cart_id, key)


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


def _guest_order() -> str:
    """The uuid of a guest order that exists in this store."""
    return str(OrderFactory(user=None).uuid)


def _payment_key(throttle_class, order_uuid, *, user=None, **headers):
    request = APIRequestFactory().post(
        f"/api/v1/order/1/create_checkout_session?uuid={order_uuid}",
        headers=headers,
    )
    request.user = user if user is not None else AnonymousUser()
    return throttle_class().get_cache_key(request, view=None)


class _GatewayPaymentKeyTests:
    """The gateway asks for a payment link right after placing an online
    order; keyed on the platform's address, the sixth agent order in a
    minute would be placed with no link. Keyed on the order instead, when
    it exists and the uuid is spelled the way guest authorization reads it
    (``Order.opens_for_guest``)."""

    throttle_class: type

    def setUp(self) -> None:
        self.order_uuid = _guest_order()

    def _pkey(self, order_uuid=None, **headers):
        return _payment_key(
            self.throttle_class,
            order_uuid or self.order_uuid,
            **headers,
        )

    def test_a_gateway_request_keys_on_the_orders_hash(self) -> None:
        key = self._pkey(**{"X-Internal-Gateway": SECRET})
        self.assertIn("gw:order:", key)
        # The uuid opens a guest order: never kept in a cache key.
        self.assertNotIn(self.order_uuid, key)

    def test_two_orders_get_distinct_buckets(self) -> None:
        self.assertNotEqual(
            self._pkey(**{"X-Internal-Gateway": SECRET}),
            self._pkey(_guest_order(), **{"X-Internal-Gateway": SECRET}),
        )

    def test_an_unknown_order_keys_on_the_address(self) -> None:
        key = self._pkey(UNKNOWN_UUID, **{"X-Internal-Gateway": SECRET})
        self.assertNotIn("gw:", key)

    def test_a_spelling_authorization_refuses_keys_on_the_address(
        self,
    ) -> None:
        """One rule: only the canonical spelling opens the order, so only
        it spends the order's budget. A refused spelling is the address's,
        and cannot use up the budget of a corrected retry."""
        for spelling in (
            self.order_uuid.upper(),
            self.order_uuid.replace("-", ""),
            "{" + self.order_uuid + "}",
        ):
            with self.subTest(spelling=spelling):
                key = self._pkey(spelling, **{"X-Internal-Gateway": SECRET})
                self.assertNotIn("gw:", key)

    def test_the_throttle_and_authorization_read_one_rule(self) -> None:
        order = OrderFactory(user=None)
        for spelling in (
            str(order.uuid),
            str(order.uuid).upper(),
            str(order.uuid).replace("-", ""),
        ):
            with self.subTest(spelling=spelling):
                keyed = "gw:" in self._pkey(
                    spelling, **{"X-Internal-Gateway": SECRET}
                )
                self.assertEqual(keyed, order.opens_for_guest(spelling))


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET)
class PaymentAttemptAnonThrottleTestCase(_GatewayPaymentKeyTests, TestCase):
    throttle_class = PaymentAttemptAnonThrottle

    def test_a_signed_in_gateway_request_is_not_counted(self) -> None:
        user = type("SignedIn", (), {"is_authenticated": True, "pk": 7})()
        self.assertIsNone(
            _payment_key(
                PaymentAttemptAnonThrottle,
                self.order_uuid,
                user=user,
                **{"X-Internal-Gateway": SECRET},
            )
        )

    def test_without_the_secret_it_keys_on_the_address(self) -> None:
        key = self._pkey(**{"X-Internal-Gateway": "not-the-secret"})
        self.assertNotIn("gw:", key)
        self.assertNotIn(self.order_uuid, key)


class _GatewayUserBudgetTests:
    """The signed-in half of a pair (``UserRateThrottle``) keys an
    anonymous request on its address, so every buyer a gateway serves was
    one caller. A request carrying the gateway's secret is keyed on its
    cart (or order) instead, as the anonymous half is, when it exists;
    everyone else keeps the address.
    """

    throttle_class: type
    scope: str

    def setUp(self) -> None:
        self.factory = APIRequestFactory()
        self.cart_id = _guest_cart()

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
            **{"X-Internal-Gateway": SECRET, "X-Cart-Id": self.cart_id}
        )
        self.assertIn(f"gw:{self.cart_id}", key)
        self.assertIn(self.scope, key)

    def test_every_spelling_of_a_cart_is_one_budget(self) -> None:
        keys = {
            self._key(**{"X-Internal-Gateway": SECRET, "X-Cart-Id": spelling})
            for spelling in (self.cart_id, *_spellings(self.cart_id))
        }
        self.assertEqual(len(keys), 1)

    def test_a_cart_id_that_is_no_uuid_keys_on_the_address(self) -> None:
        key = self._key(
            **{"X-Internal-Gateway": SECRET, "X-Cart-Id": "not-a-uuid"}
        )
        self.assertNotIn("not-a-uuid", key)
        self.assertNotIn("gw:", key)

    def test_an_unknown_cart_keys_on_the_address(self) -> None:
        """A cart write or a coupon apply that names an unknown cart
        creates one: it must stay in the address's burst budget, or a
        client making up ids would never meet a limit."""
        cache = private_cache()
        gateway = {"X-Internal-Gateway": SECRET}
        key = self._key(**gateway, **{"X-Cart-Id": UNKNOWN_UUID})
        self.assertNotIn("gw:", key)
        self.assertNotIn(UNKNOWN_UUID, key)
        other = str(uuid.uuid4())
        try:
            self.assertTrue(
                self._allowed(cache, **gateway, **{"X-Cart-Id": UNKNOWN_UUID})
            )
            # Another made-up id is the same address: no fresh budget.
            self.assertFalse(
                self._allowed(cache, **gateway, **{"X-Cart-Id": other})
            )
        finally:
            cache.delete(key)

    def test_the_query_uuid_names_no_cart(self) -> None:
        """Only the payment throttles read ``?uuid=``, as an order: on a
        cart route it must not take a budget from an order that exists."""
        order_uuid = _guest_order()
        key = self._key(
            path=f"/api/v1/cart/item?uuid={order_uuid}",
            **{"X-Internal-Gateway": SECRET},
        )
        if self.throttle_class.gateway_names_order:
            self.assertIn("gw:order:", key)
        else:
            self.assertNotIn("gw:", key)

    def test_a_minted_cart_token_keys_on_its_hash_never_the_token(
        self,
    ) -> None:
        token = "a-cart-token-value"
        cache.set(_token_cache_key(token), {"cart_id": 1}, 60)
        key = self._key(**{"X-Internal-Gateway": SECRET, "X-Cart-Token": token})
        self.assertIn(f"gw:{hash_cart_token(token)}", key)
        self.assertNotIn(token, key)

    def test_a_token_that_was_never_minted_keys_on_the_address(self) -> None:
        key = self._key(
            **{"X-Internal-Gateway": SECRET, "X-Cart-Token": "made-up"}
        )
        self.assertNotIn("gw:", key)

    def test_two_gateway_carts_have_separate_budgets(self) -> None:
        cache = private_cache()
        other = _guest_cart()
        gateway = {"X-Internal-Gateway": SECRET}
        keys = [
            self._key(**gateway, **{"X-Cart-Id": cart})
            for cart in (self.cart_id, other)
        ]
        try:
            self.assertTrue(
                self._allowed(cache, **gateway, **{"X-Cart-Id": self.cart_id})
            )
            self.assertFalse(
                self._allowed(cache, **gateway, **{"X-Cart-Id": self.cart_id})
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
        headers = {"X-Cart-Id": self.cart_id}
        key = self._key(**headers)
        try:
            self.assertNotIn(self.cart_id, key)
            self.assertTrue(self._allowed(cache, **headers))
            # Another cart from the same address is the same guest.
            self.assertFalse(
                self._allowed(cache, **{"X-Cart-Id": _guest_cart()})
            )
        finally:
            cache.delete(key)

    def test_a_forged_gateway_header_without_the_secret_keys_on_the_address(
        self,
    ) -> None:
        for headers in (
            {
                "X-Internal-Gateway": "not-the-secret",
                "X-Cart-Id": self.cart_id,
            },
            {"X-Internal-Gateway": SECRET},
            {"X-Cart-Id": self.cart_id},
        ):
            with self.subTest(headers=headers):
                self.assertNotIn(self.cart_id, self._key(**headers))
                self.assertNotIn("gw:", self._key(**headers))

    @override_settings(AGENT_GATEWAY_INTERNAL_SECRET="")
    def test_an_unset_secret_never_matches(self) -> None:
        key = self._key(**{"X-Internal-Gateway": "", "X-Cart-Id": self.cart_id})
        self.assertNotIn("gw:", key)

    def test_a_signed_in_caller_keys_on_the_account(self) -> None:
        user = type("SignedIn", (), {"is_authenticated": True, "pk": 7})()
        key = self._key(
            user=user,
            **{"X-Internal-Gateway": SECRET, "X-Cart-Id": self.cart_id},
        )
        self.assertRegex(key, r"[_:]7$")
        self.assertNotIn("gw:", key)


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET)
class CartMutationThrottleTestCase(_GatewayUserBudgetTests, TestCase):
    throttle_class = CartMutationThrottle
    scope = "cart_mutation"


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET)
class CouponApplyThrottleTestCase(_GatewayUserBudgetTests, TestCase):
    throttle_class = CouponApplyThrottle
    scope = "coupon_apply"


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET)
class OrderCreateThrottleTestCase(_GatewayUserBudgetTests, TestCase):
    throttle_class = OrderCreateThrottle
    scope = "order_create"


@override_settings(AGENT_GATEWAY_INTERNAL_SECRET=SECRET)
class PaymentAttemptThrottleTestCase(
    _GatewayUserBudgetTests, _GatewayPaymentKeyTests, TestCase
):
    throttle_class = PaymentAttemptThrottle
    scope = "payment"

    def setUp(self) -> None:
        _GatewayUserBudgetTests.setUp(self)
        _GatewayPaymentKeyTests.setUp(self)
