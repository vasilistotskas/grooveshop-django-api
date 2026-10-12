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
from order.models.item import OrderItem
from order.models.order import Order
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

    def test_create_valid(self):
        self.client.force_authenticate(user=self.staff)

        payload = {
            "order": self.order.id,
            "product": self.other_product.id,
            "quantity": 3,
            "notes": "Test item creation",
        }

        response = self.client.post(
            self.get_order_item_list_url(), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        item = OrderItem.objects.get(pk=response.data["id"])
        self.assertEqual(item.order_id, self.order.id)
        self.assertEqual(item.price, self.other_product.price)
        # The line is sold at the VAT its product carries right now.
        self.assertEqual(item.vat_rate, self.other_product.vat_percent)

    def test_create_forbidden_for_owner(self):
        # Order lines are written by store staff, not by the order's owner.
        self.client.force_authenticate(user=self.user)

        payload = {
            "order": self.order.id,
            "product": self.other_product.id,
            "quantity": 3,
            "notes": "Owner attempt",
        }

        response = self.client.post(
            self.get_order_item_list_url(), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.order.items.count(), 1)

    def test_create_uses_correct_serializer(self):
        self.client.force_authenticate(user=self.staff)

        payload = {
            "order": self.order.id,
            "product": self.product.id,
            "quantity": 1,
            "notes": "Test serializer validation",
        }

        response = self.client.post(
            self.get_order_item_list_url(), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_forbidden_on_foreign_order(self):
        # A customer must not be able to attach items to another's order.
        self.client.force_authenticate(user=self.other_user)

        payload = {
            "order": self.order.id,
            "product": self.product.id,
            "quantity": 1,
        }

        response = self.client.post(
            self.get_order_item_list_url(), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(
            OrderItem.objects.filter(
                order=self.order, product=self.product, quantity=1
            ).exists()
        )

    def test_update_cannot_reassign_to_foreign_order(self):
        # The item's owner must not be able to move it into another user's
        # order via update, `order` being a writable FK.
        self.client.force_authenticate(user=self.user)

        payload = {
            "order": self.other_order.id,
            "product": self.product.id,
            "quantity": 1,
        }
        response = self.client.put(
            self.get_order_item_detail_url(self.order_item.id),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.order_id, self.order.id)

    def test_update_valid_for_store_staff(self):
        self.client.force_authenticate(user=self.staff)

        payload = {
            "order": self.order.id,
            "product": self.product.id,
            "quantity": 4,
            "notes": "Updated notes",
        }

        response = self.client.put(
            self.get_order_item_detail_url(self.order_item.id),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.quantity, 4)
        self.assertEqual(self.order_item.notes, "Updated notes")

    def test_update_forbidden_for_owner(self):
        # self.user owns self.order_item and may read it, not rewrite it.
        self.client.force_authenticate(user=self.user)

        payload = {
            "order": self.order.id,
            "product": self.product.id,
            "quantity": 4,
            "notes": "Updated notes",
        }

        response = self.client.put(
            self.get_order_item_detail_url(self.order_item.id),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.quantity, 2)
        self.assertNotEqual(self.order_item.notes, "Updated notes")

    def test_update_forbidden_for_non_owner(self):
        # other_user does not own self.order_item — expect 403/404
        self.client.force_authenticate(user=self.other_user)

        payload = {
            "order": self.order.id,
            "product": self.product.id,
            "quantity": 4,
            "notes": "Should not update",
        }

        response = self.client.put(
            self.get_order_item_detail_url(self.order_item.id),
            payload,
            format="json",
        )

        self.assertIn(
            response.status_code,
            [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND],
        )

    def test_partial_update_valid_for_store_staff(self):
        self.client.force_authenticate(user=self.staff)

        payload = {"notes": "Partially updated notes"}

        response = self.client.patch(
            self.get_order_item_detail_url(self.order_item.id),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.notes, "Partially updated notes")

    def test_partial_update_forbidden_for_owner(self):
        self.client.force_authenticate(user=self.user)

        payload = {"quantity": 1}

        response = self.client.patch(
            self.get_order_item_detail_url(self.order_item.id),
            payload,
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.quantity, 2)

    def test_partial_update_forbidden_for_non_owner(self):
        # other_user does not own self.order_item — expect 403/404
        self.client.force_authenticate(user=self.other_user)

        payload = {"notes": "Should not update"}

        response = self.client.patch(
            self.get_order_item_detail_url(self.order_item.id),
            payload,
            format="json",
        )

        self.assertIn(
            response.status_code,
            [status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND],
        )

    def test_delete_valid(self):
        # An ADMIN-role store operator can delete any order item
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.delete(
            self.get_order_item_detail_url(self.other_order_item.id)
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            OrderItem.objects.filter(pk=self.other_order_item.pk).exists()
        )

    def test_delete_forbidden_for_owner(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.delete(
            self.get_order_item_detail_url(self.order_item.id)
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(
            OrderItem.objects.filter(pk=self.order_item.pk).exists()
        )

    def test_delete_forbidden_for_staff_role(self):
        # DELETE maps to ``delete_orderitem``, which STAFF deliberately
        # does not hold ("delete is the irreversible action",
        # tenant.role_scopes); ADMIN and OWNER do.
        self.client.force_authenticate(user=self.staff)

        response = self.client.delete(
            self.get_order_item_detail_url(self.order_item.id)
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(
            OrderItem.objects.filter(pk=self.order_item.pk).exists()
        )

    def test_writes_refused_for_is_staff_residue(self):
        """``is_staff`` on a tenant-schema customer row is not a grant."""
        residue = UserAccountFactory(is_staff=True)
        self.client.force_authenticate(user=residue)

        response = self.client.post(
            self.get_order_item_list_url(),
            {
                "order": self.order.id,
                "product": self.other_product.id,
                "quantity": 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_owner_writes_refused_at_every_order_status(self):
        # Nothing about an order's status turns its owner into someone who
        # may rewrite its lines, while reading them keeps working.
        self.client.force_authenticate(user=self.user)
        detail_url = self.get_order_item_detail_url(self.order_item.id)

        for order_status in OrderStatus:
            with self.subTest(order_status=order_status.value):
                Order.objects.filter(pk=self.order.pk).update(
                    status=order_status.value
                )

                self.assertEqual(
                    self.client.get(detail_url).status_code,
                    status.HTTP_200_OK,
                )
                self.assertEqual(
                    self.client.post(
                        self.get_order_item_list_url(),
                        {
                            "order": self.order.id,
                            "product": self.other_product.id,
                            "quantity": 1,
                        },
                        format="json",
                    ).status_code,
                    status.HTTP_403_FORBIDDEN,
                )
                self.assertEqual(
                    self.client.patch(
                        detail_url, {"quantity": 1}, format="json"
                    ).status_code,
                    status.HTTP_403_FORBIDDEN,
                )
                self.assertEqual(
                    self.client.delete(detail_url).status_code,
                    status.HTTP_403_FORBIDDEN,
                )

        self.order_item.refresh_from_db()
        self.assertEqual(self.order_item.quantity, 2)
        self.assertEqual(self.order.items.count(), 1)

    def test_unauthenticated_write_denied(self):
        response = self.client.post(
            self.get_order_item_list_url(),
            {
                "order": self.order.id,
                "product": self.product.id,
                "quantity": 1,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

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

    def test_validation_errors(self):
        self.client.force_authenticate(user=self.admin_user)

        payload = {
            "order": self.order.id,
            "product": self.product.id,
            "quantity": -1,
        }

        response = self.client.post(
            self.get_order_item_list_url(), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_stock_validation(self):
        self.client.force_authenticate(user=self.admin_user)

        payload = {
            "order": self.order.id,
            "product": self.product.id,
            "quantity": 1000,
        }

        response = self.client.post(
            self.get_order_item_list_url(), payload, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


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
