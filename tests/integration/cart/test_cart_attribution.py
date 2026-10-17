"""A cart records where its shopper came from: its first touch.

``POST /cart/item`` takes the same optional ``attribution`` object as
order create. ``CartAttributionService.capture`` stores it on the add
that takes the cart from no lines to some, keeps it while the cart holds
lines, starts over after the cart was emptied, and the guest-to-account
merge keeps the earliest one.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone
from knox.models import get_token_model
from rest_framework import status
from rest_framework.test import APIClient

from cart.agent_token import mint_cart_token
from cart.factories import CartFactory, CartItemFactory
from cart.models import Cart, CartAttribution
from cart.services import CartAttributionService, CartService
from order.attribution import ClassifiedAttribution
from order.enum.attribution import OrderSourceType
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.item import OrderItem
from product.factories import ProductFactory
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

AuthToken = get_token_model()

INSTAGRAM = {"utmSource": "ig", "utmMedium": "social"}
GOOGLE = {"utmSource": "google", "clickIds": ["gclid"]}


def _product(stock=50):
    return ProductFactory(
        price=Decimal("10.00"),
        stock=stock,
        active=True,
        discount_percent=0,
        num_images=0,
        num_reviews=0,
    )


def _add(client, product, attribution=None, **headers):
    body = {"product": product.pk, "quantity": 1}
    if attribution is not None:
        body["attribution"] = attribution
    return client.post(
        reverse("cart-item-list"), body, format="json", **headers
    )


def _signed_in(user) -> APIClient:
    _auth_token, raw = AuthToken.objects.create(user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {raw}")
    return client


def _touch(source="instagram", source_type=OrderSourceType.SOCIAL):
    return ClassifiedAttribution(source_type=source_type, source=source)


class TestCaptureOnAdd:
    def test_a_guest_cart_records_the_first_add(self):
        cart = CartFactory(is_guest=True, num_cart_items=0)

        response = _add(
            APIClient(),
            _product(),
            {
                **INSTAGRAM,
                "utmCampaign": "Summer Drop",
                "referrer": "https://l.instagram.com/?u=x",
                "landingPath": "/products/7?fbclid=IwAR0abc",
            },
            HTTP_X_CART_ID=str(cart.uuid),
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        row = CartAttribution.objects.get(cart=cart)
        assert (row.source_type, row.source) == (
            OrderSourceType.CAMPAIGN,
            "instagram",
        )
        assert row.medium == "social"
        assert row.campaign == "Summer Drop"
        assert row.referrer_host == "l.instagram.com"
        assert row.landing_path == "/products/7"

    def test_a_cart_created_by_the_first_add_records_it(self):
        response = _add(APIClient(), _product(), INSTAGRAM)

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        cart = Cart.objects.get(uuid=response.json()["cartId"])
        assert CartAttribution.objects.get(cart=cart).source == "instagram"

    def test_a_signed_in_cart_records_it(self):
        user = UserAccountFactory(num_addresses=0)
        cart = CartFactory(user=user, num_cart_items=0)

        response = _add(_signed_in(user), _product(), GOOGLE)

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        row = CartAttribution.objects.get(cart=cart)
        assert (row.source_type, row.source) == (
            OrderSourceType.PAID,
            "google",
        )

    def test_the_second_add_does_not_overwrite_it(self):
        cart = CartFactory(is_guest=True, num_cart_items=0)
        headers = {"HTTP_X_CART_ID": str(cart.uuid)}
        client = APIClient()
        _add(client, _product(), INSTAGRAM, **headers)
        first = CartAttribution.objects.get(cart=cart)

        response = _add(client, _product(), GOOGLE, **headers)

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        row = CartAttribution.objects.get(cart=cart)
        assert row.source == "instagram"
        assert row.created_at == first.created_at

    def test_refilling_a_cleared_cart_captures_anew(self):
        cart = CartFactory(is_guest=True, num_cart_items=0)
        headers = {"HTTP_X_CART_ID": str(cart.uuid)}
        client = APIClient()
        _add(client, _product(), INSTAGRAM, **headers)
        first = CartAttribution.objects.get(cart=cart)
        cart.items.all().delete()

        response = _add(client, _product(), GOOGLE, **headers)

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        row = CartAttribution.objects.get(cart=cart)
        assert (row.source_type, row.source) == (
            OrderSourceType.PAID,
            "google",
        )
        assert row.created_at > first.created_at

    def test_an_add_without_attribution_records_nothing(self):
        cart = CartFactory(is_guest=True, num_cart_items=0)

        response = _add(APIClient(), _product(), HTTP_X_CART_ID=str(cart.uuid))

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert not CartAttribution.objects.exists()

    def test_the_cart_token_path_works_without_attribution(self):
        user = UserAccountFactory(num_addresses=0)
        cart = CartFactory(user=user, num_cart_items=0)
        auth_token, _client = AuthToken.objects.create(user)
        token = mint_cart_token(cart, auth_token)
        gateway = APIClient()
        gateway.credentials(HTTP_X_CART_TOKEN=token)

        response = _add(gateway, _product())

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert cart.items.count() == 1
        assert not CartAttribution.objects.exists()

    def test_the_cart_token_path_records_a_capture_it_is_given(self):
        user = UserAccountFactory(num_addresses=0)
        cart = CartFactory(user=user, num_cart_items=0)
        auth_token, _raw = AuthToken.objects.create(user)
        gateway = APIClient()
        gateway.credentials(HTTP_X_CART_TOKEN=mint_cart_token(cart, auth_token))

        response = _add(gateway, _product(), INSTAGRAM)

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert CartAttribution.objects.get(cart=cart).source == "instagram"

    def test_an_unknown_click_id_is_rejected_like_the_order_rejects_it(self):
        cart = CartFactory(is_guest=True, num_cart_items=0)

        response = _add(
            APIClient(),
            _product(),
            {"clickIds": ["utm_id"]},
            HTTP_X_CART_ID=str(cart.uuid),
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not CartAttribution.objects.exists()
        assert not cart.items.exists()

    def test_an_agent_claim_without_the_gateway_secret_is_ignored(self):
        cart = CartFactory(is_guest=True, num_cart_items=0)

        response = _add(
            APIClient(),
            _product(),
            {"agentProtocol": "acp", "utmSource": "ig"},
            HTTP_X_CART_ID=str(cart.uuid),
        )

        assert response.status_code == status.HTTP_201_CREATED, response.json()
        assert (
            CartAttribution.objects.get(cart=cart).source_type
            == OrderSourceType.CAMPAIGN
        )

    def test_the_response_does_not_expose_it(self):
        response = _add(APIClient(), _product(), INSTAGRAM)

        assert "attribution" not in response.json()


class TestMergeKeepsTheEarliest:
    def _carts(self):
        user = UserAccountFactory(num_addresses=0)
        target = CartFactory(user=user, num_cart_items=0)
        guest = CartFactory(is_guest=True, num_cart_items=0)
        return user, target, guest

    def _merge(self, guest, user):
        request = RequestFactory().get("/", HTTP_X_CART_ID=str(guest.uuid))
        request.user = user
        CartService(request)

    def _fill(self, cart, touch, *, at):
        CartAttributionService.capture(cart, touch, touched_at=at)
        CartItemFactory(cart=cart, product=_product(), quantity=1)

    def test_the_older_guest_source_wins_over_the_account_cart(self):
        user, target, guest = self._carts()
        now = timezone.now()
        self._fill(target, _touch("google", OrderSourceType.SEARCH), at=now)
        self._fill(guest, _touch("instagram"), at=now - timedelta(days=2))

        self._merge(guest, user)

        assert not Cart.objects.filter(pk=guest.pk).exists()
        row = CartAttribution.objects.get(cart=target)
        assert row.source == "instagram"
        assert row.created_at == now - timedelta(days=2)

    def test_the_older_account_source_wins_over_the_guest_cart(self):
        user, target, guest = self._carts()
        now = timezone.now()
        self._fill(
            target,
            _touch("google", OrderSourceType.SEARCH),
            at=now - timedelta(days=2),
        )
        self._fill(guest, _touch("instagram"), at=now)

        self._merge(guest, user)

        assert CartAttribution.objects.get(cart=target).source == "google"

    def test_an_empty_account_cart_takes_the_guest_source(self):
        user, target, guest = self._carts()
        self._fill(guest, _touch("instagram"), at=timezone.now())

        self._merge(guest, user)

        assert CartAttribution.objects.get(cart=target).source == "instagram"
        assert target.items.count() == 1

    def test_an_account_cart_without_a_source_takes_the_guest_one(self):
        user, target, guest = self._carts()
        CartItemFactory(cart=target, product=_product(), quantity=1)
        self._fill(guest, _touch("instagram"), at=timezone.now())

        self._merge(guest, user)

        assert CartAttribution.objects.get(cart=target).source == "instagram"

    def test_a_guest_cart_without_a_source_leaves_the_account_one(self):
        user, target, guest = self._carts()
        now = timezone.now()
        self._fill(target, _touch("google", OrderSourceType.SEARCH), at=now)
        CartItemFactory(cart=guest, product=_product(), quantity=1)

        self._merge(guest, user)

        assert CartAttribution.objects.get(cart=target).source == "google"


class TestReorderCapture:
    def _reorder(self, user, **headers):
        order = OrderFactory(
            user=user,
            num_order_items=0,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
        )
        OrderItem.objects.create(
            order=order,
            product=_product(),
            price=_product().price,
            quantity=1,
        )
        return _signed_in(user).post(
            reverse("order-reorder", kwargs={"pk": order.pk}), **headers
        )

    def test_a_reorder_into_an_empty_cart_records_a_first_touch(self):
        user = UserAccountFactory(num_addresses=0)

        response = self._reorder(user, HTTP_USER_AGENT="Instagram 330.0.0.40")

        assert response.status_code == status.HTTP_200_OK, response.content
        row = CartAttribution.objects.get(cart__user=user)
        assert (row.source_type, row.source) == (
            OrderSourceType.SOCIAL,
            "instagram",
        )

    def test_a_reorder_into_a_filled_cart_keeps_its_source(self):
        user = UserAccountFactory(num_addresses=0)
        cart = CartFactory(user=user, num_cart_items=0)
        CartAttributionService.capture(
            cart, _touch("google", OrderSourceType.SEARCH)
        )
        CartItemFactory(cart=cart, product=_product(), quantity=1)

        response = self._reorder(user)

        assert response.status_code == status.HTTP_200_OK, response.content
        assert CartAttribution.objects.get(cart=cart).source == "google"
