import uuid
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from core.enum import FloorChoicesEnum, LocationChoicesEnum
from country.factories import CountryFactory
from order.enum.status import OrderStatus, PaymentStatus
from order.exceptions import InsufficientStockError, InvalidOrderDataError
from order.factories.order import OrderFactory
from order.models.order import Order
from order.serializers.order import (
    OrderDetailSerializer,
    OrderSerializer,
)
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory
from tests.utils import TestURLFixerMixin
from tests.utils.checkout import UNQUOTED, quoted_body
from tests.utils.shipping import enable_rate
from user.factories.account import UserAccountFactory

User = get_user_model()


pytestmark = pytest.mark.usefixtures("checkout_legal_documents")


class OrderViewSetTestCase(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory(num_addresses=0)
        cls.admin_user = UserAccountFactory(
            is_staff=True, is_superuser=True, num_addresses=0
        )
        cls.other_user = UserAccountFactory(num_addresses=0)

        cls.pay_way = PayWayFactory(active=True)
        cls.country = CountryFactory()
        cls.region = RegionFactory(country=cls.country)
        # A ShippingRate is per-country — this ad-hoc country has none
        # until this call.
        enable_rate(cls.country)

        cls.order = OrderFactory(
            user=cls.user,
            status=OrderStatus.PENDING,
            pay_way=cls.pay_way,
            country=cls.country,
            region=cls.region,
            first_name="Quillon",
            email="quillon.orders@example.com",
            city="Portland",
            tracking_number="",
            num_order_items=0,
        )
        cls.products = ProductFactory.create_batch(
            2, active=True, num_images=0, num_reviews=0, stock=20
        )
        for product, quantity in zip(cls.products, (2, 3), strict=True):
            cls.order.items.create(
                product=product,
                price=Money("50.00", settings.DEFAULT_CURRENCY),
                quantity=quantity,
            )

        cls.other_order = OrderFactory(
            user=cls.other_user,
            status=OrderStatus.SHIPPED,
            city="Chicago",
            first_name="Jane",
            last_name="Smith",
            email="jane@example.com",
            tracking_number="TRACK123",
            payment_status=PaymentStatus.COMPLETED,
            num_order_items=0,
        )

        cls.list_url = reverse("order-list")

    def detail_url(self, order_id):
        return reverse("order-detail", kwargs={"pk": order_id})

    def _address_payload(self, **overrides):
        payload = {
            "pay_way": self.pay_way.id,
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "email": f"updated-{uuid.uuid4().hex[:8]}@example.com",
            "first_name": "Updated",
            "last_name": "Name",
            "street": "456 New St",
            "street_number": "10",
            "city": "Boston",
            "zipcode": "02101",
            "phone": "+12345678902",
        }
        payload.update(overrides)
        return payload

    # -- serializers ------------------------------------------------------

    def test_list_uses_the_list_serializer(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data["results"][0].keys()),
            set(OrderSerializer.Meta.fields),
        )

    def test_retrieve_uses_the_detail_serializer(self):
        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(self.detail_url(self.order.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.order.id)
        self.assertEqual(
            set(response.data.keys()), set(OrderDetailSerializer().fields)
        )

    def test_list_no_n_plus_one(self):
        """Order-list query count must not grow with the number of orders
        or their line items (G0226)."""
        self.client.force_authenticate(user=self.admin_user)

        with CaptureQueriesContext(connection) as small:
            self.client.get(self.list_url)

        for _ in range(2):
            order = OrderFactory(
                user=self.user,
                status=OrderStatus.PENDING,
                pay_way=self.pay_way,
                country=self.country,
                region=self.region,
                num_order_items=0,
            )
            for product in ProductFactory.create_batch(
                2, active=True, num_images=1, num_reviews=2, stock=20
            ):
                order.items.create(
                    product=product,
                    price=Money("10.00", settings.DEFAULT_CURRENCY),
                    quantity=1,
                )

        with CaptureQueriesContext(connection) as large:
            self.client.get(self.list_url)

        self.assertEqual(
            len(small),
            len(large),
            f"Query count grew from {len(small)} to {len(large)} when "
            f"orders/items grew — N+1 regression.",
        )

    # -- create -----------------------------------------------------------

    def test_create_order_from_cart(self):
        self.client.force_authenticate(user=self.admin_user)
        cart = CartFactory.create(user=self.admin_user)
        CartItemFactory.create(cart=cart, product=self.products[0], quantity=1)
        payload = {
            "pay_way_id": self.pay_way.id,
            "terms_accepted": True,
            "country_id": self.country.alpha_2,
            "region_id": self.region.alpha,
            "floor": FloorChoicesEnum.FIRST_FLOOR.value,
            "location_type": LocationChoicesEnum.HOME.value,
            "email": "new-order@example.com",
            "first_name": "New",
            "last_name": "Order",
            "street": "789 Test St",
            "street_number": "5",
            "city": "Seattle",
            "zipcode": "98101",
            "phone": "+12345678903",
            "shipping_kind": "home_delivery",
        }

        response = self.client.post(
            self.list_url,
            quoted_body(self.client, payload),
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            f"order creation failed: {response.data}",
        )
        self.assertEqual(
            set(response.data.keys()), set(OrderDetailSerializer().fields)
        )
        created_order = Order.objects.get(email="new-order@example.com")
        self.assertEqual(created_order.id, response.data["id"])
        self.assertEqual(created_order.city, "Seattle")
        self.assertEqual(
            list(created_order.items.values_list("product_id", "quantity")),
            [(self.products[0].id, 1)],
        )

    def test_create_reports_every_invalid_field(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.post(
            self.list_url,
            {"email": "invalid-email", "phone": "invalid-phone"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        for field in (
            "pay_way_id",
            "email",
            "phone",
            "first_name",
            "expected_total",
        ):
            self.assertIn(field, response.data)

    # -- update -----------------------------------------------------------

    def test_update_order(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.put(
            self.detail_url(self.order.id),
            self._address_payload(city="Boston"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data.keys()), set(OrderDetailSerializer().fields)
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.first_name, "Updated")
        self.assertEqual(self.order.city, "Boston")

    def test_update_does_not_mutate_line_items(self):
        """Order line items must NOT be delete+recreated by an order
        update — that bypassed stock accounting and total recomputation
        (G0222). The original items (with their committed stock) stay."""
        self.client.force_authenticate(user=self.admin_user)
        original_items = dict(self.order.items.values_list("id", "quantity"))

        response = self.client.put(
            self.detail_url(self.order.id),
            self._address_payload(
                items=[{"product": self.products[0].id, "quantity": 1}]
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.city, "Boston")
        self.assertEqual(
            dict(self.order.items.values_list("id", "quantity")),
            original_items,
        )

    def test_partial_update(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.patch(
            self.detail_url(self.order.id),
            {"city": "Updated City"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(response.data.keys()), set(OrderDetailSerializer().fields)
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.city, "Updated City")

    def test_owner_cannot_reassign_order_to_another_user(self):
        # ``user`` is read-only on OrderWriteSerializer: a raw setattr
        # mass-assign previously let an owner PATCH their order onto
        # another account (or null it to a guest), taking their PII with
        # it. The address edit still applies; the user FK must not move.
        self.client.force_authenticate(user=self.user)

        response = self.client.patch(
            self.detail_url(self.order.id),
            {"user": self.other_user.id, "city": "Reassigned City"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.user_id, self.user.id)
        self.assertEqual(self.order.city, "Reassigned City")

    # -- delete -----------------------------------------------------------

    def test_admin_can_delete_order(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.delete(self.detail_url(self.order.id))

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Order.objects.filter(id=self.order.id).exists())

    def test_owner_cannot_delete_own_order(self):
        # ``destroy`` is admin-only: DRF's default destroy soft-deletes
        # the row without releasing stock, refunding, or cancelling the
        # courier voucher — bypassing OrderLifecycleService.cancel_order. A
        # non-staff owner must not reach it (customers cancel, not
        # delete). Regression for the owner-reachable destroy hole.
        self.client.force_authenticate(user=self.user)

        response = self.client.delete(self.detail_url(self.order.id))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Order.objects.filter(id=self.order.id).exists())

    # -- access -----------------------------------------------------------

    def test_admin_lists_every_order(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {o["id"] for o in response.data["results"]},
            {self.order.id, self.other_order.id},
        )

    def test_customer_lists_only_own_orders(self):
        # get_queryset filters by user for non-staff, preventing
        # cross-user IDOR enumeration of the order list.
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [o["id"] for o in response.data["results"]], [self.order.id]
        )

    def test_my_orders_returns_only_own_orders(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(reverse("order-my-orders"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [o["id"] for o in response.data["results"]], [self.order.id]
        )

    def test_customer_cannot_retrieve_another_users_order(self):
        self.client.force_authenticate(user=self.user)

        own = self.client.get(self.detail_url(self.order.id))
        other = self.client.get(self.detail_url(self.other_order.id))

        self.assertEqual(own.status_code, status.HTTP_200_OK)
        self.assertEqual(other.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_retrieve_any_order(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(self.detail_url(self.other_order.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.other_order.id)

    def test_retrieve_by_uuid(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            reverse(
                "order-retrieve-by-uuid", kwargs={"uuid": str(self.order.uuid)}
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.order.id)

    def test_retrieve_nonexistent_order(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(self.detail_url(99999))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_access_denied(self):
        for url in (
            self.list_url,
            self.detail_url(self.order.id),
            reverse("order-my-orders"),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(
                    response.status_code, status.HTTP_401_UNAUTHORIZED
                )

    # -- search -----------------------------------------------------------

    def test_search(self):
        self.client.force_authenticate(user=self.admin_user)

        for term, expected in (
            ("Quillon", self.order.id),
            ("quillon.orders@example.com", self.order.id),
            ("TRACK123", self.other_order.id),
            ("Chicago", self.other_order.id),
        ):
            with self.subTest(term=term):
                response = self.client.get(self.list_url, {"search": term})
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(
                    [o["id"] for o in response.data["results"]], [expected]
                )

    # -- actions ----------------------------------------------------------

    def test_cancel_action_cancels_a_pending_order(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.post(
            reverse("order-cancel", kwargs={"pk": self.order.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.CANCELED)

    def test_add_tracking_action(self):
        self.client.force_authenticate(user=self.admin_user)
        Order.objects.filter(id=self.order.id).update(
            status=OrderStatus.PROCESSING
        )

        response = self.client.post(
            reverse("order-add-tracking", kwargs={"pk": self.order.id}),
            {"tracking_number": "TRACK123456", "shipping_carrier": "FedEx"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.tracking_number, "TRACK123456")
        self.assertEqual(self.order.shipping_carrier, "FedEx")

    def test_update_status_action(self):
        self.client.force_authenticate(user=self.admin_user)
        url = reverse("order-update-status", kwargs={"pk": self.order.id})

        refused = self.client.post(
            url, {"status": OrderStatus.COMPLETED}, format="json"
        )
        self.order.refresh_from_db()
        self.assertEqual(refused.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.order.status, OrderStatus.PENDING)

        allowed = self.client.post(
            url, {"status": OrderStatus.PROCESSING}, format="json"
        )
        self.order.refresh_from_db()
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(self.order.status, OrderStatus.PROCESSING)

    def test_staff_actions_are_forbidden_to_the_owner(self):
        self.client.force_authenticate(user=self.user)

        for name, payload in (
            (
                "order-add-tracking",
                {"tracking_number": "T1", "shipping_carrier": "C"},
            ),
            ("order-update-status", {"status": OrderStatus.SHIPPED}),
        ):
            with self.subTest(action=name):
                response = self.client.post(
                    reverse(name, kwargs={"pk": self.order.id}), payload
                )
                self.assertEqual(
                    response.status_code, status.HTTP_403_FORBIDDEN
                )

        self.order.refresh_from_db()
        self.assertEqual(self.order.status, OrderStatus.PENDING)
        self.assertEqual(self.order.tracking_number, "")


class OnlineOrderCreationTestCase(APITestCase):
    """``POST /order`` for an online pay way (Stripe): the order is created
    first, PENDING and holding no payment, and paid afterwards on the
    hosted page (#818)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory(num_addresses=0)
        cls.pay_way = PayWayFactory.create_online_payment()
        cls.country = CountryFactory()
        cls.region = RegionFactory(country=cls.country)
        # A ShippingRate is per-country — this ad-hoc country has none
        # until this call.
        enable_rate(cls.country)

        cls.product1 = ProductFactory(
            active=True, stock=10, num_images=0, num_reviews=0
        )
        cls.product2 = ProductFactory(
            active=True, stock=5, num_images=0, num_reviews=0
        )
        cls.cart = CartFactory(user=cls.user)
        CartItemFactory(cart=cls.cart, product=cls.product1, quantity=2)
        CartItemFactory(cart=cls.cart, product=cls.product2, quantity=1)
        cls.guest_cart = CartFactory(user=None)
        CartItemFactory(cart=cls.guest_cart, product=cls.product1, quantity=1)

        cls.create_url = reverse("order-list")

    def setUp(self):
        super().setUp()
        # These tests run outside any tenant context (public schema),
        # where stripe_credentials() has no fallback at all — provide a
        # stand-in tenant key so the "stripe" pay way is treated as
        # configured (this suite tests order creation, not Stripe
        # credential resolution).
        patcher = patch(
            "tenant.credentials.stripe_credentials",
            return_value={
                "secret_key": "sk_test_dummy_tenant_key",
                "publishable_key": "pk_test_dummy_tenant_key",
                "live_mode": False,
            },
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _order_data(self, **overrides):
        data = {
            "pay_way_id": self.pay_way.id,
            "terms_accepted": True,
            "first_name": "John",
            "last_name": "Doe",
            "email": "john.doe@example.com",
            "street": "Main Street",
            "street_number": "123",
            "city": "Athens",
            "zipcode": "12345",
            "country_id": self.country.alpha_2,
            "region_id": self.region.alpha,
            "phone": "+306900000000",
            "shipping_kind": "home_delivery",
        }
        data.update(overrides)
        return data

    def _post(self, data, cart=None, *, expected_total=None):
        """Place the order at ``expected_total``, or else at its quote's."""
        headers = {"HTTP_X_CART_ID": str((cart or self.cart).uuid)}
        if expected_total is None:
            data = quoted_body(self.client, data, **headers)
        else:
            data = {**data, "expected_total": expected_total}
        return self.client.post(self.create_url, data, format="json", **headers)

    def test_creates_the_order_for_the_signed_in_shopper(self):
        self.client.force_authenticate(user=self.user)

        with patch("order.payments.payment.get_payment_provider") as provider:
            response = self._post(self._order_data())

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.data
        )
        # No money moves before the order exists: no provider is asked.
        provider.assert_not_called()
        self.assertEqual(response.data["status"], OrderStatus.PENDING)
        self.assertEqual(response.data["user"], self.user.id)
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(str(order.uuid), response.data["uuid"])
        self.assertEqual(order.payment_status, PaymentStatus.PENDING)
        self.assertFalse(order.payment_id)
        self.assertEqual(
            dict(order.items.values_list("product_id", "quantity")),
            {self.product1.id: 2, self.product2.id: 1},
        )
        # The cart is kept for the payment that follows.
        self.assertTrue(self.cart.items.exists())

    def test_creates_a_guest_order_from_the_guest_cart(self):
        response = self._post(self._order_data(), cart=self.guest_cart)

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.data
        )
        self.assertIsNone(response.data["user"])
        order = Order.objects.get(id=response.data["id"])
        self.assertFalse(order.payment_id)
        self.assertEqual(
            list(order.items.values_list("product_id", "quantity")),
            [(self.product1.id, 1)],
        )

    def test_the_view_does_not_wrap_the_carrier_call_in_a_transaction(self):
        """The service owns the one transaction. A view-level
        ``transaction.atomic`` would put the carrier's live-price HTTP
        call back inside one, idle on a connection that
        ``idle_in_transaction_session_timeout`` kills."""
        from order.services import OrderService

        baseline_depth = len(connection.savepoint_ids)
        depths = []
        resolve = OrderService._resolve_live_shipping

        def recording(*args, **kwargs):
            depths.append(len(connection.savepoint_ids))
            return resolve(*args, **kwargs)

        self.client.force_authenticate(user=self.user)

        with patch.object(
            OrderService, "_resolve_live_shipping", side_effect=recording
        ):
            response = self._post(self._order_data())

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.data
        )
        self.assertEqual(depths, [baseline_depth])

    def test_request_field_errors_are_reported_per_field(self):
        self.client.force_authenticate(user=self.user)
        for field, data in (
            ("first_name", self._order_data(first_name="")),
            ("email", self._order_data(email="invalid-email")),
            ("pay_way_id", self._order_data(pay_way_id=None)),
            ("pay_way_id", self._order_data(pay_way_id=99999)),
        ):
            with self.subTest(field=field, value=data[field]):
                response = self._post(data, expected_total=UNQUOTED)

                self.assertEqual(
                    response.status_code, status.HTTP_400_BAD_REQUEST
                )
                self.assertIn(field, response.data)

        self.assertFalse(Order.objects.filter(user=self.user).exists())

    @patch("order.services.OrderService.create_order_from_cart")
    def test_insufficient_stock_error_maps_to_a_typed_400(
        self, mock_create_order
    ):
        mock_create_order.side_effect = InsufficientStockError(
            product_id=self.product1.id, available=5, requested=10
        )
        self.client.force_authenticate(user=self.user)

        response = self._post(self._order_data())

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)
        self.assertEqual(
            response.data["error"],
            {
                "type": "insufficient_stock",
                "product_id": self.product1.id,
                "available": 5,
                "requested": 10,
            },
        )

    @patch("order.services.OrderService.create_order_from_cart")
    def test_invalid_order_data_error_maps_to_a_typed_400(
        self, mock_create_order
    ):
        mock_create_order.side_effect = InvalidOrderDataError(
            "Invalid order data",
            field_errors={"product": ["Product not found"]},
        )
        self.client.force_authenticate(user=self.user)

        response = self._post(self._order_data())

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["type"], "invalid_order_data")
        self.assertEqual(response.data["code"], "invalid_order_data")
        self.assertEqual(
            response.data["field_errors"], {"product": ["Product not found"]}
        )


def _checkout_session_url(order_pk, uuid_value=None):
    url = reverse("order-create-checkout-session", kwargs={"pk": order_pk})
    return f"{url}?uuid={uuid_value}" if uuid_value else url


def _unpaid_stripe_order(**fields):
    return OrderFactory(
        num_order_items=0,
        status=OrderStatus.PENDING,
        payment_status=PaymentStatus.PENDING,
        payment_id="",
        pay_way=PayWayFactory.create_online_payment(),
        **fields,
    )


@pytest.mark.django_db
class TestPaymentRoutePermissions:
    """A pk-addressed payment route opens only the caller's own order, or a
    guest's order with its uuid (``check_object_permissions``); a refused
    caller never reaches the provider."""

    BODY = {
        "successUrl": "https://shop.example/ok",
        "cancelUrl": "https://shop.example/no",
    }

    def _post(self, url, user=None):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        with (
            patch(
                "pay_way.services.PayWayService.is_provider_configured",
                return_value=True,
            ),
            patch("order.views.order.get_payment_provider") as provider,
        ):
            response = client.post(url, self.BODY, format="json")
        return response, provider

    def test_another_users_order_is_forbidden(self):
        order = _unpaid_stripe_order(user=UserAccountFactory())

        response, provider = self._post(
            _checkout_session_url(order.pk), user=UserAccountFactory()
        )

        assert response.status_code == 403
        provider.assert_not_called()

    def test_anonymous_caller_cannot_pay_a_users_order(self):
        order = _unpaid_stripe_order(user=UserAccountFactory())

        response, provider = self._post(
            _checkout_session_url(order.pk, order.uuid)
        )

        assert response.status_code == 403
        provider.assert_not_called()

    def test_guest_order_without_uuid_is_forbidden(self):
        order = _unpaid_stripe_order(user=None, email="guest@example.com")

        response, provider = self._post(_checkout_session_url(order.pk))

        assert response.status_code == 403
        provider.assert_not_called()

    def test_guest_order_with_wrong_uuid_is_forbidden(self):
        order = _unpaid_stripe_order(user=None, email="guest@example.com")
        other = _unpaid_stripe_order(user=None, email="other@example.com")

        response, provider = self._post(
            _checkout_session_url(order.pk, other.uuid)
        )

        assert response.status_code == 403
        provider.assert_not_called()

    def test_unknown_order_is_404(self):
        response, provider = self._post(
            _checkout_session_url(987654321), user=UserAccountFactory()
        )

        assert response.status_code == 404
        provider.assert_not_called()
