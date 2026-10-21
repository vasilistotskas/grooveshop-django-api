"""The cart-scoped agent token (``POST /api/v1/cart/agent-token``).

The shopping assistant reaches a signed-in shopper's cart with an
``X-Cart-Token`` header on anonymous requests. These tests pin the wire
behaviour: what the token may do, everything it must refuse, and that a
refusal never turns into a substitute guest cart.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.core.cache.backends.locmem import LocMemCache
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from knox.models import get_token_model
from rest_framework import status
from rest_framework.test import APIClient

from cart.agent_token import (
    CartTokenInvalid,
    CartTokenNotPermitted,
    mint_cart_token,
    resolve_cart_token,
)
from cart.factories import CartFactory, CartItemFactory
from cart.models import Cart
from cart.services import CartService
from core.api.throttling import CartAgentTokenThrottle
from product.factories import ProductFactory
from tests.utils.staff import (
    bind_store_tenant,
    store_tenant,
    unbind_store_tenant,
)
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

AuthToken = get_token_model()


def _login(user):
    """A real knox login: the instance and a client sending its bearer."""
    auth_token, raw = AuthToken.objects.create(user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return auth_token, client


def _mint(client) -> str:
    response = client.post(reverse("cart-agent-token"))
    assert response.status_code == status.HTTP_200_OK, response.content
    return response.data["token"]


def _gateway(token: str | None) -> APIClient:
    """What the agent gateway is: anonymous, carrying only the token."""
    client = APIClient()
    if token is not None:
        client.credentials(HTTP_X_CART_TOKEN=token)
    return client


def _product(price="10.00", stock=50):
    return ProductFactory(
        price=Decimal(price), stock=stock, active=True, discount_percent=0
    )


@pytest.fixture
def shopper():
    user = UserAccountFactory(num_addresses=0)
    cart = CartFactory(user=user, num_cart_items=0)
    auth_token, client = _login(user)
    return user, cart, auth_token, client


class TestMint:
    def test_requires_authentication(self):
        response = APIClient().post(reverse("cart-agent-token"))

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )
        assert not Cart.objects.exists()

    def test_requires_a_knox_login(self):
        """Forced/session auth has no knox token to bind the cart
        token to, so it cannot be revoked by logout: refused."""
        user = UserAccountFactory(num_addresses=0)
        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(reverse("cart-agent-token"))

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_returns_token_and_ttl_for_own_cart(self, shopper):
        _user, cart, _auth, client = shopper

        response = client.post(reverse("cart-agent-token"))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["token"]
        assert (
            response.data["expires_in"] == settings.CART_AGENT_TOKEN_TTL_SECONDS
        )
        read = _gateway(response.data["token"]).get(reverse("cart-detail"))
        assert read.status_code == status.HTTP_200_OK
        assert read.data["id"] == cart.pk

    def test_creates_the_cart_when_the_shopper_has_none(self):
        user = UserAccountFactory(num_addresses=0)
        Cart.objects.filter(user=user).delete()
        _auth, client = _login(user)

        token = _mint(client)

        cart = Cart.objects.get(user=user)
        read = _gateway(token).get(reverse("cart-detail"))
        assert read.data["id"] == cart.pk

    def test_each_mint_is_a_new_token(self, shopper):
        client = shopper[3]

        assert _mint(client) != _mint(client)

    def test_cache_holds_a_hash_never_the_token(self, shopper):
        _user, _cart, _auth, client = shopper
        token = _mint(client)

        redis = cache._cache.get_client(write=False)
        keys = [k.decode() for k in redis.scan_iter("*cart:agent-token:*")]

        assert keys
        assert not any(token in key for key in keys)


class TestAllowedActions:
    def test_reads_the_cart(self, shopper):
        _user, cart, _auth, client = shopper
        item = CartItemFactory(cart=cart, product=_product(), quantity=2)

        response = _gateway(_mint(client)).get(reverse("cart-detail"))

        assert response.status_code == status.HTTP_200_OK
        assert [line["id"] for line in response.data["items"]] == [item.pk]

    def test_adds_updates_and_removes_items(self, shopper):
        _user, cart, _auth, client = shopper
        product = _product()
        gateway = _gateway(_mint(client))
        carts_before = Cart.objects.count()

        added = gateway.post(
            reverse("cart-item-list"),
            {"product": product.pk, "quantity": 2},
            format="json",
        )
        assert added.status_code == status.HTTP_201_CREATED, added.content
        item = cart.items.get(product=product)
        assert item.quantity == 2

        updated = gateway.patch(
            reverse("cart-item-detail", args=[item.pk]),
            {"quantity": 5},
            format="json",
        )
        assert updated.status_code == status.HTTP_200_OK, updated.content
        item.refresh_from_db()
        assert item.quantity == 5

        listed = gateway.get(reverse("cart-item-list"))
        assert listed.status_code == status.HTTP_200_OK
        assert listed.data["count"] == 1

        removed = gateway.delete(reverse("cart-item-detail", args=[item.pk]))
        assert removed.status_code == status.HTTP_204_NO_CONTENT
        assert not cart.items.exists()
        # Every call landed on the shopper's cart; none made another.
        assert Cart.objects.count() == carts_before

    def test_cannot_touch_another_carts_item(self, shopper):
        _user, _cart, _auth, client = shopper
        foreign = CartItemFactory(
            cart=CartFactory(user=UserAccountFactory(num_addresses=0)),
            product=_product(),
        )

        response = _gateway(_mint(client)).delete(
            reverse("cart-item-detail", args=[foreign.pk])
        )

        assert response.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        )
        assert foreign.cart.items.filter(pk=foreign.pk).exists()

    def test_applies_and_removes_a_coupon(self, shopper):
        from promotion.factories import PromotionCodeFactory, PromotionFactory

        _user, cart, _auth, client = shopper
        CartItemFactory(cart=cart, product=_product("100.00"), quantity=1)
        code = PromotionCodeFactory(
            promotion=PromotionFactory(benefit_value=Decimal(10))
        )
        gateway = _gateway(_mint(client))

        def _get(key, default=None):
            return {"PROMOTIONS_ENABLED": True}.get(key, default)

        with patch("promotion.services.Setting.get", side_effect=_get):
            applied = gateway.post(
                reverse("cart-coupon"), {"code": code.code}, format="json"
            )
            listed = gateway.get(reverse("cart-coupons"))
            removed = gateway.delete(reverse("cart-coupon"))

        assert applied.status_code == status.HTTP_200_OK, applied.content
        assert applied.data["id"] == cart.pk
        assert listed.status_code == status.HTTP_200_OK
        assert removed.status_code == status.HTTP_200_OK
        from promotion.models import CartPromotionCode

        assert not CartPromotionCode.objects.filter(cart=cart).exists()

    def test_applies_a_personal_coupon_for_the_cart_owner(self, shopper):
        """The request is anonymous, so the coupon must be judged against
        the cart's owner: a code assigned to that shopper applies, and
        is listed, though ``request.user`` is nobody."""
        from promotion.factories import PromotionCodeFactory, PromotionFactory

        user, cart, _auth, client = shopper
        CartItemFactory(cart=cart, product=_product("100.00"), quantity=1)
        code = PromotionCodeFactory(
            promotion=PromotionFactory(benefit_value=Decimal(10)),
            assigned_to=user,
        )
        gateway = _gateway(_mint(client))

        def _get(key, default=None):
            return {"PROMOTIONS_ENABLED": True}.get(key, default)

        with patch("promotion.services.Setting.get", side_effect=_get):
            listed = gateway.get(reverse("cart-coupons"))
            applied = gateway.post(
                reverse("cart-coupon"), {"code": code.code}, format="json"
            )

        assert applied.status_code == status.HTTP_200_OK, applied.content
        assert code.code in [row["code"] for row in listed.data]

    def test_a_personal_coupon_of_someone_else_is_refused(self, shopper):
        from promotion.factories import PromotionCodeFactory, PromotionFactory

        _user, cart, _auth, client = shopper
        CartItemFactory(cart=cart, product=_product("100.00"), quantity=1)
        code = PromotionCodeFactory(
            promotion=PromotionFactory(benefit_value=Decimal(10)),
            assigned_to=UserAccountFactory(num_addresses=0),
        )
        gateway = _gateway(_mint(client))

        def _get(key, default=None):
            return {"PROMOTIONS_ENABLED": True}.get(key, default)

        with patch("promotion.services.Setting.get", side_effect=_get):
            applied = gateway.post(
                reverse("cart-coupon"), {"code": code.code}, format="json"
            )

        assert applied.status_code == status.HTTP_400_BAD_REQUEST
        assert applied.data["reason"] == "discount_code_user_ineligible"

    def test_token_wins_over_cart_id(self, shopper):
        _user, cart, _auth, client = shopper
        guest = CartFactory(user=None, num_cart_items=0)
        product = _product()
        gateway = _gateway(_mint(client))

        response = gateway.post(
            reverse("cart-item-list"),
            {"product": product.pk, "quantity": 1},
            format="json",
            HTTP_X_CART_ID=str(guest.uuid),
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert cart.items.filter(product=product).exists()
        assert not guest.items.exists()


class TestShippingReads:
    """``/shipping/options`` and the nearest-locker lookup read the cart
    only to tell whether it fits a carrier's parcels, so the token works
    there like on ``GET /cart``."""

    @staticmethod
    def _oversize(cart):
        from measurement.measures import Distance

        product = _product()
        product.length = Distance(cm=80)
        product.width = Distance(cm=50)
        product.height = Distance(cm=40)
        product.save()
        CartItemFactory(cart=cart, product=product, quantity=1)

    @staticmethod
    def _boxnow_option(client, **headers):
        from shipping.models import ShippingProvider

        ShippingProvider.objects.filter(code="boxnow").update(is_active=True)
        response = client.get(
            reverse("shipping-options"), {"country_code": "GR"}, **headers
        )
        if response.status_code != status.HTTP_200_OK:
            return response, None
        return response, next(
            o for o in response.json() if o["providerCode"] == "boxnow"
        )

    @staticmethod
    def _nearest(client, **headers):
        from unittest.mock import MagicMock

        boxnow = MagicMock()
        boxnow.return_value.find_closest_locker.return_value = {
            "id": "4",
            "type": "apm",
        }
        with patch("shipping_boxnow.client.BoxNowClient", boxnow):
            response = client.post(
                reverse("shipping-boxnow-locker-nearest"),
                {"city": "A", "street": "B 1", "postalCode": "15232"},
                format="json",
                **headers,
            )
        return response, boxnow

    def test_options_flag_the_owners_cart(
        self, shopper, boxnow_configured_tenant
    ):
        _user, cart, _auth, client = shopper
        self._oversize(cart)

        response, option = self._boxnow_option(_gateway(_mint(client)))

        assert response.status_code == status.HTTP_200_OK
        assert option["exceedsParcelSize"] is True

    def test_nearest_asks_for_the_owners_size(self, shopper):
        from measurement.measures import Distance

        _user, cart, _auth, client = shopper
        product = _product()
        product.length = Distance(cm=30)
        product.width = Distance(cm=20)
        product.height = Distance(cm=12)
        product.save()
        CartItemFactory(cart=cart, product=product, quantity=1)

        response, boxnow = self._nearest(_gateway(_mint(client)))

        assert response.status_code == status.HTTP_200_OK
        kwargs = boxnow.return_value.find_closest_locker.call_args.kwargs
        assert kwargs["compartment_size"] == 2

    def test_nearest_refuses_the_owners_oversize_cart(self, shopper):
        _user, cart, _auth, client = shopper
        self._oversize(cart)

        response, boxnow = self._nearest(_gateway(_mint(client)))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        boxnow.return_value.find_closest_locker.assert_not_called()

    def test_an_invalid_token_is_a_401_never_a_guest_cart(
        self, shopper, boxnow_configured_tenant
    ):
        guest = CartFactory(user=None, num_cart_items=0)
        self._oversize(guest)
        carts_before = Cart.objects.count()
        gateway = _gateway("not-a-minted-token")

        options, _option = self._boxnow_option(
            gateway, HTTP_X_CART_ID=str(guest.uuid)
        )
        nearest, boxnow = self._nearest(gateway, HTTP_X_CART_ID=str(guest.uuid))

        for response in (options, nearest):
            assert response.status_code == status.HTTP_401_UNAUTHORIZED
        boxnow.return_value.find_closest_locker.assert_not_called()
        assert Cart.objects.count() == carts_before

    def test_the_token_wins_over_cart_id(
        self, shopper, boxnow_configured_tenant
    ):
        """The owner's cart fits; the guest cart named by ``X-Cart-Id``
        does not. The flag follows the token."""
        _user, cart, _auth, client = shopper
        guest = CartFactory(user=None, num_cart_items=0)
        self._oversize(guest)

        _response, option = self._boxnow_option(
            _gateway(_mint(client)), HTTP_X_CART_ID=str(guest.uuid)
        )

        assert option["exceedsParcelSize"] is False
        assert not cart.items.exists()

    def test_the_token_still_gets_403_outside_the_allowlist(self, shopper):
        _user, _cart, _auth, client = shopper
        response = _gateway(_mint(client)).post(
            reverse("cart-reserve-stock"), {}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["detail"].code == "cart_token_not_permitted"


class TestRefusedActions:
    @pytest.mark.parametrize(
        ("method", "url_name"),
        [
            ("post", "cart-reserve-stock"),
            ("post", "cart-release-reservations"),
            ("post", "cart-create-payment-intent"),
            ("delete", "cart-detail"),
            ("put", "cart-detail"),
            ("patch", "cart-detail"),
            ("post", "cart-agent-token"),
        ],
    )
    def test_outside_the_allowlist_is_refused(self, shopper, method, url_name):
        _user, cart, _auth, client = shopper
        CartItemFactory(cart=cart, product=_product(), quantity=1)
        gateway = _gateway(_mint(client))

        response = getattr(gateway, method)(
            reverse(url_name), {}, format="json"
        )

        # The mint endpoint needs a login before it gets as far as the
        # token; every other route refuses the token itself.
        if url_name == "cart-agent-token":
            assert response.status_code in (
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_403_FORBIDDEN,
            )
        else:
            assert response.status_code == status.HTTP_403_FORBIDDEN
            assert response.data["detail"].code == "cart_token_not_permitted"
        assert Cart.objects.filter(pk=cart.pk).exists()
        assert cart.items.count() == 1

    def test_a_plain_cart_service_does_not_accept_the_token(self, shopper):
        """Order create builds ``CartService(request)`` with no opt-in,
        so the header cannot select the cart it checks out."""
        _user, _cart, _auth, client = shopper
        request = RequestFactory().post("/", HTTP_X_CART_TOKEN=_mint(client))
        request.user = AnonymousUser()

        with pytest.raises(CartTokenNotPermitted):
            CartService(request)

    def test_token_does_not_authenticate_the_account(self, shopper):
        user, _cart, _auth, client = shopper
        gateway = _gateway(_mint(client))
        url = reverse("user-account-detail", args=[user.pk])

        with_token = gateway.get(url)
        without = APIClient().get(url)

        assert with_token.status_code == without.status_code
        assert with_token.status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        )


class TestRefusedTokens:
    def _assert_refused_without_new_cart(self, token):
        carts_before = Cart.objects.count()
        gateway = _gateway(token)
        product = _product()

        read = gateway.get(reverse("cart-detail"))
        add = gateway.post(
            reverse("cart-item-list"),
            {"product": product.pk, "quantity": 1},
            format="json",
        )
        coupon = gateway.delete(reverse("cart-coupon"))

        for response in (read, add, coupon):
            assert response.status_code == status.HTTP_401_UNAUTHORIZED
            assert response.data["detail"].code == "cart_token_invalid"
        assert Cart.objects.count() == carts_before

    def test_unknown_token(self, shopper):
        self._assert_refused_without_new_cart("not-a-minted-token")

    @override_settings(CART_AGENT_TOKEN_TTL_SECONDS=1)
    def test_expired_token(self, shopper):
        token = _mint(shopper[3])
        assert _gateway(token).get(reverse("cart-detail")).status_code == 200

        time.sleep(1.5)

        self._assert_refused_without_new_cart(token)

    def test_logout_revokes_the_token(self, shopper):
        _user, _cart, auth_token, client = shopper
        token = _mint(client)
        assert _gateway(token).get(reverse("cart-detail")).status_code == 200

        AuthToken.objects.filter(pk=auth_token.pk).delete()

        self._assert_refused_without_new_cart(token)

    def test_expired_login_revokes_the_token(self, shopper):
        _user, _cart, auth_token, client = shopper
        token = _mint(client)

        AuthToken.objects.filter(pk=auth_token.pk).update(
            expiry=timezone.now() - timedelta(seconds=1)
        )

        self._assert_refused_without_new_cart(token)

    def test_login_past_the_absolute_age_cap_revokes_the_token(self, shopper):
        _user, _cart, auth_token, client = shopper
        token = _mint(client)

        AuthToken.objects.filter(pk=auth_token.pk).update(
            created=timezone.now() - settings.KNOX_ABSOLUTE_MAX_AGE * 2
        )

        self._assert_refused_without_new_cart(token)

    def test_cart_handed_to_someone_else(self, shopper):
        _user, cart, _auth, client = shopper
        token = _mint(client)

        Cart.objects.filter(pk=cart.pk).update(
            user=UserAccountFactory(num_addresses=0)
        )

        self._assert_refused_without_new_cart(token)

    def test_deleted_cart(self, shopper):
        _user, cart, _auth, client = shopper
        token = _mint(client)

        cart.delete()

        self._assert_refused_without_new_cart(token)

    def test_deactivated_owner(self, shopper):
        user, _cart, _auth, client = shopper
        token = _mint(client)

        type(user).objects.filter(pk=user.pk).update(is_active=False)

        self._assert_refused_without_new_cart(token)


class TestTenantScope:
    def test_token_minted_on_one_store_is_unknown_on_another(self):
        user = UserAccountFactory(num_addresses=0)
        cart = CartFactory(user=user, num_cart_items=0)
        auth_token, _raw = AuthToken.objects.create(user)
        tenant_a = store_tenant("cart_token_a")
        tenant_b = store_tenant("cart_token_b")

        previous = bind_store_tenant(tenant_a)
        try:
            token = mint_cart_token(cart, auth_token)
            assert resolve_cart_token(token).pk == cart.pk
            bind_store_tenant(tenant_b)
            with pytest.raises(CartTokenInvalid):
                resolve_cart_token(token)
        finally:
            unbind_store_tenant(previous)


class TestNeverLogged:
    def test_token_is_absent_from_every_log_record(self, shopper, caplog):
        _user, _cart, _auth, client = shopper
        product = _product()
        caplog.set_level(logging.DEBUG)

        token = _mint(client)
        gateway = _gateway(token)
        gateway.get(reverse("cart-detail"))
        gateway.post(
            reverse("cart-item-list"),
            {"product": product.pk, "quantity": 1},
            format="json",
        )
        gateway.post(reverse("cart-reserve-stock"))  # a refusal
        _gateway("a-bad-token-value").get(reverse("cart-detail"))

        assert caplog.records
        assert token not in caplog.text
        assert "a-bad-token-value" not in caplog.text


class TestMintThrottle:
    def test_scope_is_budgeted_and_fails_closed(self):
        assert CartAgentTokenThrottle.scope == "cart_agent_token"
        assert CartAgentTokenThrottle.fail_closed is True
        rates = settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]
        assert "cart_agent_token" in rates

    def test_a_signed_in_caller_runs_out_of_mints(self, shopper, monkeypatch):
        # A private cache and a class-level rate: the suite runs with
        # every rate disabled and Redis shared across workers (the
        # pattern of the b2b profile-submit throttle test).
        monkeypatch.setattr(
            CartAgentTokenThrottle, "rate", "2/minute", raising=False
        )
        monkeypatch.setattr(
            CartAgentTokenThrottle,
            "cache",
            LocMemCache(f"cart-token-throttle-{uuid4()}", {}),
            raising=False,
        )
        client = shopper[3]

        statuses = [
            client.post(reverse("cart-agent-token")).status_code
            for _ in range(3)
        ]

        assert statuses == [
            status.HTTP_200_OK,
            status.HTTP_200_OK,
            status.HTTP_429_TOO_MANY_REQUESTS,
        ]
