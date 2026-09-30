import uuid
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories import CartFactory, CartItemFactory
from country.factories import CountryFactory
from order.enum.status import OrderStatus, PaymentStatus
from order.factories.order import OrderFactory
from order.models.order import Order
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from region.factories import RegionFactory
from tests.utils.shipping import enable_rate
from user.factories.account import UserAccountFactory


class CheckoutAPITestCase(APITestCase):
    """Order-first checkout (``POST /order`` with an offline pay way)."""

    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory(num_addresses=0)
        cls.country = CountryFactory(num_regions=0)
        cls.region = RegionFactory(country=cls.country)
        # A ShippingRate is per-country — this ad-hoc country has none
        # until this call.
        enable_rate(cls.country)
        cls.pay_way = PayWayFactory.create_offline_payment(
            provider_code="cash", requires_confirmation=False
        )
        cls.product1 = ProductFactory.create(
            stock=20, num_images=0, num_reviews=0, active=True
        )
        cls.product2 = ProductFactory.create(
            stock=15, num_images=0, num_reviews=0, active=True
        )
        cls.checkout_url = reverse("order-list")

    def _checkout_data(self, **overrides):
        data = {
            "email": "customer@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "phone": "+12025550195",
            "street": "Main Street",
            "street_number": "123",
            "city": "Testville",
            "zipcode": "12345",
            "country_id": self.country.alpha_2,
            "region_id": self.region.alpha,
            "pay_way_id": self.pay_way.id,
            "shipping_kind": "home_delivery",
        }
        data.update(overrides)
        return data

    def _cart(self, user=None, items=None):
        cart = CartFactory(user=user)
        for product, quantity in items or (
            (self.product1, 2),
            (self.product2, 1),
        ):
            CartItemFactory(cart=cart, product=product, quantity=quantity)
        return cart

    def _post(self, data, cart):
        return self.client.post(
            self.checkout_url,
            data,
            format="json",
            HTTP_X_CART_ID=str(cart.uuid),
        )

    def test_guest_checkout_creates_an_unowned_order(self):
        cart = self._cart()

        response = self._post(self._checkout_data(), cart)

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.data
        )
        self.assertIsNone(response.data["user"])
        order = Order.objects.get(id=response.data["id"])
        self.assertEqual(order.email, "customer@example.com")
        self.assertEqual((order.first_name, order.last_name), ("John", "Doe"))
        self.assertEqual(order.status, OrderStatus.PENDING)
        self.assertEqual(
            dict(order.items.values_list("product_id", "quantity")),
            {self.product1.id: 2, self.product2.id: 1},
        )

    def test_signed_in_checkout_creates_the_shoppers_order(self):
        self.client.force_authenticate(user=self.user)
        cart = self._cart(user=self.user)

        response = self._post(self._checkout_data(), cart)

        self.assertEqual(
            response.status_code, status.HTTP_201_CREATED, response.data
        )
        self.assertEqual(response.data["user"], self.user.id)
        self.assertEqual(
            Order.objects.get(id=response.data["id"]).user_id, self.user.id
        )

    def test_insufficient_stock_is_refused_without_touching_stock(self):
        scarce = ProductFactory.create(
            stock=5, num_images=0, num_reviews=0, active=True
        )
        cart = self._cart(items=((scarce, 10),))

        response = self._post(self._checkout_data(), cart)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cart", response.data)
        scarce.refresh_from_db()
        self.assertEqual(scarce.stock, 5)

    def test_invalid_email_is_a_field_error(self):
        cart = self._cart()

        response = self._post(self._checkout_data(email="invalid-email"), cart)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)
        self.assertFalse(Order.objects.filter(email="invalid-email").exists())


class GuestOrderAccessTestCase(APITestCase):
    """A guest order is reachable only through its unguessable UUID."""

    @classmethod
    def setUpTestData(cls):
        cls.user = UserAccountFactory(num_addresses=0)
        cls.admin_user = UserAccountFactory(
            is_staff=True, is_superuser=True, num_addresses=0
        )
        cls.guest_order = OrderFactory(
            user=None,
            email="guest@example.com",
            status=OrderStatus.PENDING,
            num_order_items=0,
        )
        cls.user_order = OrderFactory(
            user=cls.user,
            email=cls.user.email,
            status=OrderStatus.PENDING,
            num_order_items=0,
        )

    def _by_uuid_url(self, order):
        return reverse(
            "order-retrieve-by-uuid", kwargs={"uuid": str(order.uuid)}
        )

    def test_guest_can_retrieve_their_order_by_uuid(self):
        response = self.client.get(self._by_uuid_url(self.guest_order))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["uuid"], str(self.guest_order.uuid))
        self.assertEqual(response.data["email"], "guest@example.com")
        self.assertIsNone(response.data["user"])

    def test_path_uuid_is_authoritative_over_query_param(self):
        """The path param is the endpoint's identity — a stray ``?uuid=``
        query must not override it and break access to the correctly
        addressed order."""
        response = self.client.get(
            self._by_uuid_url(self.guest_order),
            {"uuid": "00000000-0000-0000-0000-000000000000"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["uuid"], str(self.guest_order.uuid))

    def test_guest_cannot_retrieve_a_registered_users_order(self):
        response = self.client.get(self._by_uuid_url(self.user_order))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_guest_can_cancel_their_order_with_its_uuid(self):
        url = (
            reverse("order-cancel", kwargs={"pk": self.guest_order.pk})
            + f"?uuid={self.guest_order.uuid}"
        )

        response = self.client.post(
            url, {"reason": "Changed my mind"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.guest_order.refresh_from_db()
        self.assertEqual(self.guest_order.status, OrderStatus.CANCELED)

    def test_guest_cannot_cancel_by_integer_pk_alone(self):
        """Regression for the guest-order IDOR: the cancel/payment
        actions are keyed by sequential integer pk, so access must
        require the order's UUID (via ?uuid=). Missing or wrong -> 403."""
        cancel_url = reverse("order-cancel", kwargs={"pk": self.guest_order.pk})

        for url in (cancel_url, cancel_url + f"?uuid={uuid.uuid4()}"):
            with self.subTest(url=url):
                response = self.client.post(
                    url, {"reason": "IDOR attempt"}, format="json"
                )
                self.assertEqual(
                    response.status_code, status.HTTP_403_FORBIDDEN
                )

        self.guest_order.refresh_from_db()
        self.assertEqual(self.guest_order.status, OrderStatus.PENDING)

    def test_guest_cannot_cancel_a_registered_users_order(self):
        response = self.client.post(
            reverse("order-cancel", kwargs={"pk": self.user_order.pk}),
            {"reason": "Trying to cancel"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.user_order.refresh_from_db()
        self.assertEqual(self.user_order.status, OrderStatus.PENDING)

    def test_signed_in_user_cannot_retrieve_a_guest_order_by_id(self):
        self.client.force_authenticate(user=self.user)

        response = self.client.get(
            reverse("order-detail", kwargs={"pk": self.guest_order.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_retrieve_a_guest_order(self):
        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(
            reverse("order-detail", kwargs={"pk": self.guest_order.pk})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["id"], self.guest_order.id)
        self.assertIsNone(response.data["user"])

    def test_guest_can_create_a_payment_intent_with_the_uuid(self):
        order = OrderFactory(
            user=None,
            email="guest@example.com",
            pay_way=PayWayFactory(provider_code="stripe"),
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            num_order_items=0,
        )
        order.items.create(
            product=ProductFactory(stock=10, num_images=0, num_reviews=0),
            price=Money(Decimal("50.00"), settings.DEFAULT_CURRENCY),
            quantity=2,
        )
        url = (
            reverse("order-create-payment-intent", kwargs={"pk": order.pk})
            + f"?uuid={order.uuid}"
        )

        with patch(
            "pay_way.services.PayWayService.process_payment"
        ) as mock_payment:
            mock_payment.return_value = (
                True,
                {
                    "payment_id": "pi_test123",
                    "status": "requires_payment_method",
                    "amount": "100.00",
                    "currency": "EUR",
                    "provider": "stripe",
                    "client_secret": "pi_test123_secret",
                },
            )
            response = self.client.post(url, {}, format="json")

        self.assertEqual(
            response.status_code, status.HTTP_200_OK, response.data
        )
        self.assertEqual(response.data["payment_id"], "pi_test123")

    def test_payment_intent_forwards_the_named_fields_to_the_provider(self):
        """``paymentMethodId`` / ``customerId`` / ``returnUrl`` reach the
        PSP call as keyword arguments, next to ``paymentData``."""
        order = OrderFactory(
            user=None,
            pay_way=PayWayFactory(provider_code="stripe"),
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            num_order_items=0,
        )
        url = (
            reverse("order-create-payment-intent", kwargs={"pk": order.pk})
            + f"?uuid={order.uuid}"
        )

        with patch(
            "pay_way.services.PayWayService.process_payment",
            return_value=(
                True,
                {
                    "payment_id": "pi_named",
                    "status": "requires_payment_method",
                    "amount": "10.00",
                    "currency": "EUR",
                    "provider": "stripe",
                    "client_secret": "pi_named_secret",
                },
            ),
        ) as mock_payment:
            response = self.client.post(
                url,
                {
                    "paymentMethodId": "pm_123",
                    "customerId": "cus_123",
                    "returnUrl": "https://shop.example/checkout/return",
                    "paymentData": {"save_card": "true"},
                },
                format="json",
            )

        self.assertEqual(
            response.status_code, status.HTTP_200_OK, response.data
        )
        mock_payment.assert_called_once()
        kwargs = mock_payment.call_args.kwargs
        self.assertEqual(kwargs.pop("order").pk, order.pk)
        self.assertEqual(kwargs.pop("pay_way").pk, order.pay_way_id)
        self.assertEqual(
            kwargs,
            {
                "save_card": "true",
                "payment_method_id": "pm_123",
                "customer_id": "cus_123",
                "return_url": "https://shop.example/checkout/return",
            },
        )
