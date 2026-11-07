from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APITestCase

from order.enum.status import (
    RETURN_HANDLING_STATUSES,
    OrderStatus,
    PaymentStatus,
)
from order.factories.order import OrderFactory
from order.models.stock_log import StockLog
from order.serializers.item import (
    OrderItemDetailSerializer,
    OrderItemSerializer,
)
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from tenant.models import TenantMembershipRole
from tests.utils import TestURLFixerMixin
from tests.utils.staff import (
    bind_store_tenant,
    store_staff,
    store_tenant,
    unbind_store_tenant,
)
from user.factories.account import UserAccountFactory

User = get_user_model()


class OrderItemViewSetTestCase(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory()
        cls.other_user = UserAccountFactory()
        # Order lines are written by STORE STAFF only: a stamped platform
        # identity with a role in the tenant bound to the connection
        # (``setUp``). STAFF may add and change lines; only ADMIN/OWNER
        # hold ``delete_orderitem`` (tenant.role_scopes).
        cls.tenant = store_tenant("order_item_tenant")
        cls.staff = store_staff(cls.tenant)
        cls.admin_user = store_staff(
            cls.tenant, role=TenantMembershipRole.ADMIN
        )

        cls.pay_way = PayWayFactory()

        cls.order = OrderFactory(
            num_order_items=0, user=cls.user, pay_way=cls.pay_way
        )

        cls.other_order = OrderFactory(
            num_order_items=0, user=cls.other_user, pay_way=cls.pay_way
        )

        cls.product = ProductFactory(
            active=True, num_images=0, num_reviews=0, stock=20
        )
        cls.other_product = ProductFactory(
            active=True, num_images=0, num_reviews=0, stock=15
        )

        cls.order_item = cls.order.items.create(
            product=cls.product,
            price=Money("50.00", "EUR"),
            quantity=2,
        )

        cls.other_order_item = cls.other_order.items.create(
            product=cls.other_product,
            price=Money("30.00", "EUR"),
            quantity=1,
        )

    def setUp(self):
        self.addCleanup(unbind_store_tenant, bind_store_tenant(self.tenant))

    def get_order_item_detail_url(self, pk):
        return reverse("order-item-detail", kwargs={"pk": pk})

    def get_order_item_list_url(self):
        return reverse("order-item-list")

    def test_list(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.get_order_item_list_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        self.assertEqual(
            [r["id"] for r in response.data["results"]], [self.order_item.id]
        )

    def test_list_uses_correct_serializer(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.get_order_item_list_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        first_result = response.data["results"][0]
        expected_fields = set(OrderItemSerializer.Meta.fields)
        actual_fields = set(first_result.keys())
        self.assertEqual(actual_fields, expected_fields)

    def test_retrieve_valid(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(
            self.get_order_item_detail_url(self.order_item.id)
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.order_item.id)
        self.assertEqual(response.data["quantity"], 2)

    def test_retrieve_uses_correct_serializer(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(
            self.get_order_item_detail_url(self.order_item.id)
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected_fields = set(OrderItemDetailSerializer.Meta.fields)
        actual_fields = set(response.data.keys())
        self.assertEqual(actual_fields, expected_fields)

    def test_retrieve_invalid(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.get_order_item_detail_url(99999))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def _wholesale_order(self, group_id: int):
        order = OrderFactory(
            num_order_items=0, user=self.user, pay_way=self.pay_way
        )
        order.metadata = {
            **(order.metadata or {}),
            "b2b_pricing": {"group_id": group_id},
        }
        order.save(update_fields=["metadata"])
        return order

    def test_user_can_only_access_own_order_items(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_detail_url(self.other_order_item.id)
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_can_access_all_order_items(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(
            self.get_order_item_detail_url(self.order_item.id)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(
            self.get_order_item_detail_url(self.other_order_item.id)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unauthenticated_access_denied(self):
        response = self.client.get(self.get_order_item_list_url())
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_filter_by_order(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_list_url(), {"order": self.order.id}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [r["id"] for r in response.data["results"]], [self.order_item.id]
        )

    def test_filter_by_product(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_list_url(), {"product": self.product.id}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["product"], self.product.id
        )

    def test_filter_by_quantity_range(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_list_url(), {"quantity_min": 2}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [r["id"] for r in response.data["results"]], [self.order_item.id]
        )

    def test_filter_by_refund_status(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_list_url(), {"is_refunded": "false"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [r["id"] for r in response.data["results"]], [self.order_item.id]
        )

    def test_search_functionality(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_list_url(), {"search": "test"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

    def test_ordering_functionality(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            self.get_order_item_list_url(), {"ordering": "created_at"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

    def test_the_write_routes_are_gone(self):
        """A line is a record of what was sold: nothing edits it through the
        API (a restock is the one change, ``order-items/<id>/restock``)."""
        self.client.force_authenticate(user=self.staff)
        detail = self.get_order_item_detail_url(self.order_item.pk)
        listing = self.get_order_item_list_url()

        for method, url in (
            ("post", listing),
            ("put", detail),
            ("patch", detail),
            ("delete", detail),
        ):
            response = getattr(self.client, method)(url, {}, format="json")
            assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

        self.order_item.refresh_from_db()
        assert self.order_item.quantity == 2


@pytest.mark.assert_english
class RestockActionTests(TestURLFixerMixin, APITestCase):
    """
    Tests for the restock action on OrderItemViewSet.

    It put returned units back in stock and records them on the line; it
    moves NO money (it was ``refund`` until the payment ledger, and its
    "Refund processed successfully" claimed money that never moved). What
    the customer is paid back is the order's refund.

    Uses per-test setUp (not setUpTestData) because each test must set
    a specific order status before calling the restock endpoint — mutating
    shared class-level state in setUpTestData causes test-order coupling.
    """

    def setUp(self):
        self.user = UserAccountFactory()
        # Restocking is a STORE STAFF operation: a stamped platform identity
        # with a staff role in the tenant bound to this connection.
        self.tenant = store_tenant("refund_tenant")
        self.staff = store_staff(self.tenant)
        self.addCleanup(unbind_store_tenant, bind_store_tenant(self.tenant))
        self.pay_way = PayWayFactory()
        self.product = ProductFactory(
            active=True, num_images=0, num_reviews=0, stock=20
        )
        self.order = OrderFactory(
            num_order_items=0,
            user=self.user,
            pay_way=self.pay_way,
            payment_status=PaymentStatus.COMPLETED,
        )
        self.order_item = self.order.items.create(
            product=self.product,
            price=Money("50.00", "EUR"),
            quantity=2,
        )

    def get_restock_url(self, pk):
        return reverse("order-item-restock", kwargs={"pk": pk})

    def test_restock_action_forbidden_for_non_staff(self):
        # Restocking changes inventory and the line's returned quantity — a
        # staff-only operation. The order owner must not be able to
        # self-serve it.
        self.client.force_authenticate(user=self.user)

        self.order.status = OrderStatus.DELIVERED.value
        self.order.save()

        response = self.client.post(
            self.get_restock_url(self.order_item.id),
            {"quantity": 1, "reason": "self restock"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.refunded_quantity, 0)

    def test_restock_action_refused_for_is_staff_residue(self):
        """``is_staff`` on a tenant-schema customer row is not a grant."""
        residue = UserAccountFactory(is_staff=True)
        self.client.force_authenticate(user=residue)

        self.order.status = OrderStatus.DELIVERED.value
        self.order.save()

        response = self.client.post(
            self.get_restock_url(self.order_item.id),
            {"quantity": 1, "reason": "Product defective"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.refunded_quantity, 0)

    def test_restock_action_valid(self):
        self.client.force_authenticate(user=self.staff)

        self.order.status = OrderStatus.DELIVERED.value
        self.order.save()

        payload = {"quantity": 1, "reason": "Product defective"}
        response = self.client.post(
            self.get_restock_url(self.order_item.id),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["detail"], "The returned units were restocked."
        )
        self.assertEqual(response.data["item"]["refunded_quantity"], 1)
        self.order_item.refresh_from_db()
        self.assertIn(
            "Restock reason: Product defective", self.order_item.notes
        )
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 21)

    def test_restock_action_records_who_and_why_on_the_stock_log(self):
        self.client.force_authenticate(user=self.staff)
        self.order.status = OrderStatus.DELIVERED.value
        self.order.save()

        response = self.client.post(
            self.get_restock_url(self.order_item.id),
            {"quantity": 1, "reason": "Product defective"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        log = StockLog.objects.get(
            product=self.product,
            order_id=self.order.id,
            operation_type=StockLog.OPERATION_INCREMENT,
        )
        # Staff are platform identities: named by id, never as a store user.
        self.assertEqual(log.performed_by_staff_id, self.staff.pk)
        self.assertIsNone(log.performed_by_id)
        self.assertIn("Product defective", log.reason)

    def test_restock_reason_longer_than_the_stock_log_allows_is_refused(self):
        self.client.force_authenticate(user=self.staff)
        self.order.status = OrderStatus.DELIVERED.value
        self.order.save()

        response = self.client.post(
            self.get_restock_url(self.order_item.id),
            {"quantity": 1, "reason": "x" * 151},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 20)

    def test_restock_error_detail_is_a_string(self):
        self.client.force_authenticate(user=self.staff)
        self.order.status = OrderStatus.PROCESSING.value
        self.order.save()

        response = self.client.post(
            self.get_restock_url(self.order_item.id),
            {"quantity": 1},
            format="json",
        )

        self.assertIsInstance(response.data["detail"], str)

    def test_restock_action_refused_before_delivery_says_why(self):
        self.client.force_authenticate(user=self.staff)
        self.order.status = OrderStatus.PROCESSING.value
        self.order.save()

        response = self.client.post(
            self.get_restock_url(self.order_item.id),
            {"quantity": 1},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("delivered", str(response.data))
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock, 20)

    def test_restock_action_moves_no_money(self):
        """The answer says nothing about a refunded amount and nothing is
        written to the ledger or the order's payment."""
        self.client.force_authenticate(user=self.staff)
        self.order.status = OrderStatus.DELIVERED.value
        self.order.save()

        with patch("order.services.order_refunded.send") as refunded_signal:
            response = self.client.post(
                self.get_restock_url(self.order_item.id),
                {"quantity": 2},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("refunded_amount", response.data)
        self.assertFalse(self.order.payment_transactions.exists())
        refunded_signal.assert_not_called()
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, PaymentStatus.COMPLETED)

    def test_restock_action_allowed_while_staff_handle_a_return(self):
        self.client.force_authenticate(user=self.staff)

        for order_status in RETURN_HANDLING_STATUSES:
            with self.subTest(status=order_status):
                self.order.status = order_status
                self.order.save()
                self.order_item.refunded_quantity = 0
                self.order_item.is_refunded = False
                self.order_item.save()

                response = self.client.post(
                    self.get_restock_url(self.order_item.id),
                    {"quantity": 1},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_restock_action_invalid_order_status(self):
        self.client.force_authenticate(user=self.staff)

        self.order.status = OrderStatus.PENDING.value
        self.order.save()

        payload = {"quantity": 1, "reason": "Test restock"}
        response = self.client.post(
            self.get_restock_url(self.order_item.id),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_restock_action_excessive_quantity(self):
        self.client.force_authenticate(user=self.staff)

        self.order.status = OrderStatus.DELIVERED.value
        self.order.save()

        payload = {"quantity": 10, "reason": "Test excessive restock"}
        response = self.client.post(
            self.get_restock_url(self.order_item.id),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
