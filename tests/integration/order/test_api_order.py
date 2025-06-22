import json
from decimal import Decimal
from unittest.mock import patch

from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from country.factories import CountryFactory
from order.enum.status import OrderStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory

User = UserAccountFactory._meta.model


class CheckoutAPITestCase(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = UserAccountFactory(num_addresses=0)
        self.country = CountryFactory(num_regions=0)
        self.region = RegionFactory(country=self.country)
        self.pay_way = PayWayFactory()

        self.product1 = ProductFactory.create(
            stock=20, num_images=0, num_reviews=0, active=True
        )
        self.product2 = ProductFactory.create(
            stock=15, num_images=0, num_reviews=0, active=True
        )

        self.currency = str(self.product1.price.currency)

        self.checkout_url = reverse("order-list")
        self.checkout_data = {
            "email": "customer@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "phone": "+12025550195",
            "street": "Main Street",
            "street_number": "123",
            "city": "Testville",
            "zipcode": "12345",
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "pay_way": self.pay_way.id,
            "shipping_price": "10.00",
            "items": [
                {"product": self.product1.id, "quantity": 2},
                {"product": self.product2.id, "quantity": 1},
            ],
        }

    @patch("order.signals.order_created.send")
    def test_checkout_successful(self, mock_signal):
        response = self.client.post(
            self.checkout_url,
            data=json.dumps(self.checkout_data),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertEqual(Order.objects.count(), 1)

        self.product1.refresh_from_db()
        self.product2.refresh_from_db()

        self.assertEqual(self.product1.stock, 20 - 2)
        self.assertEqual(self.product2.stock, 15 - 1)

        self.assertTrue(mock_signal.called)

    def test_checkout_insufficient_stock(self):
        product_limited = ProductFactory.create(
            stock=5, num_images=0, num_reviews=0, active=True
        )

        data = self.checkout_data.copy()
        data["items"] = [{"product": product_limited.id, "quantity": 10}]

        response = self.client.post(
            self.checkout_url,
            data=json.dumps(data),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("items", response.data)

        product_limited.refresh_from_db()
        self.assertEqual(product_limited.stock, 5)

    def test_checkout_authenticated_user(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.post(
            self.checkout_url,
            data=json.dumps(self.checkout_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        order = Order.objects.first()
        self.assertEqual(order.user, self.user)

    def test_checkout_invalid_data(self):
        invalid_data = self.checkout_data.copy()
        invalid_data["email"] = "invalid-email"

        response = self.client.post(
            self.checkout_url,
            data=json.dumps(invalid_data),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

        self.assertEqual(Order.objects.count(), 0)


class OrderViewSetTestCase(APITestCase):
    def setUp(self):
        self.client = APIClient()

        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpassword",
        )

        self.admin_user = User.objects.create_user(
            username="adminuser",
            email="admin@example.com",
            password="adminpassword",
            is_staff=True,
            is_superuser=True,
        )

        self.order1 = OrderFactory(user=self.user)
        self.order2 = OrderFactory(user=self.user)
        self.order3 = OrderFactory()

        product = ProductFactory(stock=10)
        self.order1.items.create(
            product=product,
            price=Money(amount=Decimal("50.00"), currency="USD"),
            quantity=2,
        )

        self.orders_url = reverse("order-list")
        self.order1_url = reverse("order-detail", kwargs={"pk": self.order1.pk})
        self.order_uuid_url = reverse(
            "order-retrieve-by-uuid", kwargs={"uuid": str(self.order1.uuid)}
        )
        self.my_orders_url = reverse("order-my-orders")
        self.cancel_order_url = reverse(
            "order-cancel", kwargs={"pk": self.order1.pk}
        )
        self.add_tracking = reverse(
            "order-add-tracking", kwargs={"pk": self.order1.pk}
        )
        self.update_status_url = reverse(
            "order-update-status", kwargs={"pk": self.order1.pk}
        )

    def test_list_orders_unauthenticated(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(self.orders_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_list_orders_admin(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(self.orders_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), Order.objects.count())

    def test_retrieve_order_by_id(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(self.order1_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(response.data["id"], self.order1.id)

    def test_retrieve_order_by_uuid(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(self.order_uuid_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(response.data["uuid"], str(self.order1.uuid))

    def test_my_orders(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(self.my_orders_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreaterEqual(len(response.data["results"]), 1)

        order_ids = [order["id"] for order in response.data["results"]]
        self.assertNotIn(self.order3.id, order_ids)

    def test_cancel_order(self):
        self.client.force_authenticate(user=self.admin_user)

        self.order1.status = OrderStatus.PENDING.value
        self.order1.save()

        response = self.client.post(self.cancel_order_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.order1.refresh_from_db()
        self.assertEqual(self.order1.status, OrderStatus.CANCELED.value)

    def test_add_tracking(self):
        self.client.force_authenticate(user=self.admin_user)

        self.order1.status = OrderStatus.PROCESSING.value
        self.order1.save()

        tracking_data = {
            "tracking_number": "TRACK123456",
            "shipping_carrier": "FedEx",
        }

        response = self.client.post(
            self.add_tracking,
            data=json.dumps(tracking_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.order1.refresh_from_db()
        self.assertEqual(self.order1.tracking_number, "TRACK123456")
        self.assertEqual(self.order1.shipping_carrier, "FedEx")

    def test_update_order_status(self):
        self.client.force_authenticate(user=self.admin_user)

        self.order1.status = OrderStatus.PENDING.value
        self.order1.save()

        status_data = {"status": OrderStatus.PROCESSING.value}

        response = self.client.post(
            self.update_status_url,
            data=json.dumps(status_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.order1.refresh_from_db()
        self.assertEqual(self.order1.status, OrderStatus.PROCESSING.value)

    def test_update_order_status_invalid_transition(self):
        self.client.force_authenticate(user=self.admin_user)

        self.order1.status = OrderStatus.PENDING.value
        self.order1.save()

        status_data = {"status": OrderStatus.COMPLETED.value}

        response = self.client.post(
            self.update_status_url,
            data=json.dumps(status_data),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.order1.refresh_from_db()
        self.assertEqual(self.order1.status, OrderStatus.PENDING.value)
