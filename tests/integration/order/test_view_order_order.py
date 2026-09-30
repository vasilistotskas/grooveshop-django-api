import uuid
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from core.enum import FloorChoicesEnum, LocationChoicesEnum
from country.factories import CountryFactory
from order.enum.status import OrderStatus, PaymentStatus
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
from tests.utils.shipping import enable_rate
from user.factories.account import UserAccountFactory

User = get_user_model()


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
            set(response.data.keys()), set(OrderDetailSerializer.Meta.fields)
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
            "payment_intent_id": f"pi_test_{uuid.uuid4().hex[:8]}",
            "pay_way_id": self.pay_way.id,
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

        with patch("order.payment.get_payment_provider") as mock_get_provider:
            mock_get_provider.return_value.get_payment_status.return_value = (
                PaymentStatus.COMPLETED,
                {"status": "succeeded"},
            )
            response = self.client.post(self.list_url, payload, format="json")

        self.assertEqual(
            response.status_code,
            status.HTTP_201_CREATED,
            f"order creation failed: {response.data}",
        )
        self.assertEqual(
            set(response.data.keys()), set(OrderDetailSerializer.Meta.fields)
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
        for field in ("pay_way_id", "email", "phone", "first_name"):
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
            set(response.data.keys()), set(OrderDetailSerializer.Meta.fields)
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
            set(response.data.keys()), set(OrderDetailSerializer.Meta.fields)
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
        # courier voucher — bypassing OrderService.cancel_order. A
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
