from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from cart.factories.cart import CartFactory
from cart.factories.item import CartItemFactory
from core.utils.testing import TestURLFixerMixin
from order.models import StockReservation
from product.factories.product import ProductFactory
from user.factories.account import UserAccountFactory

User = get_user_model()


class CartStockReservationTest(TestURLFixerMixin, APITestCase):
    """Test cart stock reservation endpoints."""

    @classmethod
    def setUpTestData(cls):
        """Set up test data for all test methods."""
        cls.user = UserAccountFactory(num_addresses=0)
        cls.product1 = ProductFactory(stock=10)
        cls.product2 = ProductFactory(stock=5)
        cls.product_out_of_stock = ProductFactory(stock=0)

    def setUp(self):
        """Set up test fixtures for each test method."""
        self.client.force_authenticate(user=self.user)
        self.cart = CartFactory(user=self.user, num_cart_items=0)
        self.reserve_url = reverse("cart-reserve-stock")
        self.release_url = reverse("cart-release-reservations")

    def test_reserve_stock_success(self):
        """Test successful stock reservation for cart items."""
        # Add items to cart
        CartItemFactory(cart=self.cart, product=self.product1, quantity=3)
        CartItemFactory(cart=self.cart, product=self.product2, quantity=2)

        # Reserve stock
        response = self.client.post(self.reserve_url)

        # Verify response
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("reservation_ids", response.data)
        self.assertIn("message", response.data)
        self.assertEqual(len(response.data["reservation_ids"]), 2)

        # Verify reservations were created
        reservations = StockReservation.objects.filter(
            session_id=str(self.cart.uuid)
        )
        self.assertEqual(reservations.count(), 2)

        # Verify reservation details
        reservation1 = reservations.filter(product=self.product1).first()
        self.assertIsNotNone(reservation1)
        self.assertEqual(reservation1.quantity, 3)
        self.assertEqual(reservation1.reserved_by, self.user)
        self.assertFalse(reservation1.consumed)

    def test_reserve_stock_empty_cart(self):
        """Test reserving stock for empty cart returns error."""
        response = self.client.post(self.reserve_url)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)
        self.assertIn("empty", response.data["detail"].lower())

    def test_reserve_stock_insufficient_stock(self):
        """Test reserving stock when insufficient stock available."""
        # Add item with quantity exceeding stock
        CartItemFactory(cart=self.cart, product=self.product2, quantity=10)

        # Attempt to reserve stock
        response = self.client.post(self.reserve_url)

        # Verify error response
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)
        self.assertIn("failed_items", response.data)
        self.assertEqual(len(response.data["failed_items"]), 1)

        # Verify failed item details
        failed_item = response.data["failed_items"][0]
        self.assertEqual(failed_item["product_id"], self.product2.id)
        self.assertEqual(failed_item["available"], 5)
        self.assertEqual(failed_item["requested"], 10)

        # Verify no reservations were created
        reservations = StockReservation.objects.filter(
            session_id=str(self.cart.uuid)
        )
        self.assertEqual(reservations.count(), 0)

    def test_reserve_stock_out_of_stock_product(self):
        """Test reserving stock for out-of-stock product."""
        # Add out-of-stock item to cart
        CartItemFactory(
            cart=self.cart, product=self.product_out_of_stock, quantity=1
        )

        # Attempt to reserve stock
        response = self.client.post(self.reserve_url)

        # Verify error response
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("failed_items", response.data)

    def test_release_reservations_success(self):
        """Test successful release of stock reservations."""
        # Create reservations first
        CartItemFactory(cart=self.cart, product=self.product1, quantity=3)
        reserve_response = self.client.post(self.reserve_url)
        reservation_ids = reserve_response.data["reservation_ids"]

        # Release reservations
        response = self.client.post(
            self.release_url,
            {"reservation_ids": reservation_ids},
            format="json",
        )

        # Verify response
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("message", response.data)
        self.assertIn("released_count", response.data)
        self.assertEqual(response.data["released_count"], len(reservation_ids))

        # Verify reservations were marked as consumed (released)
        for reservation_id in reservation_ids:
            reservation = StockReservation.objects.get(id=reservation_id)
            self.assertTrue(reservation.consumed)

    def test_release_reservations_empty_list(self):
        """Test releasing reservations with empty list returns error."""
        response = self.client.post(
            self.release_url, {"reservation_ids": []}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)

    def test_release_reservations_missing_parameter(self):
        """Test releasing reservations without reservation_ids parameter."""
        response = self.client.post(self.release_url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)

    def test_release_reservations_invalid_type(self):
        """Test releasing reservations with invalid parameter type."""
        response = self.client.post(
            self.release_url, {"reservation_ids": "not-a-list"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("detail", response.data)

    def test_release_reservations_nonexistent_ids(self):
        """Test releasing reservations with non-existent IDs."""
        # Try to release non-existent reservations
        response = self.client.post(
            self.release_url, {"reservation_ids": [99999, 99998]}, format="json"
        )

        # Should return success but with failed_releases
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["released_count"], 0)
        self.assertIn("failed_releases", response.data)

    def test_guest_cart_reserve_stock(self):
        """Test stock reservation for guest cart."""
        # Create guest cart
        guest_cart = CartFactory(user=None, num_cart_items=0)
        CartItemFactory(cart=guest_cart, product=self.product1, quantity=2)

        # Authenticate as guest (no authentication)
        self.client.force_authenticate(user=None)

        # Add cart ID header for guest
        response = self.client.post(
            self.reserve_url, HTTP_X_CART_ID=str(guest_cart.id)
        )

        # Verify response
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("reservation_ids", response.data)

        # Verify reservation was created with no user
        reservations = StockReservation.objects.filter(
            session_id=str(guest_cart.uuid)
        )
        self.assertEqual(reservations.count(), 1)
        self.assertIsNone(reservations.first().reserved_by)

    def test_reserve_stock_multiple_times_same_cart(self):
        """Test reserving stock multiple times for same cart."""
        # Add item to cart
        CartItemFactory(cart=self.cart, product=self.product1, quantity=2)

        # Reserve stock first time
        response1 = self.client.post(self.reserve_url)
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        reservation_ids_1 = response1.data["reservation_ids"]

        # Reserve stock second time (should create new reservations)
        response2 = self.client.post(self.reserve_url)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        reservation_ids_2 = response2.data["reservation_ids"]

        # Verify different reservation IDs
        self.assertNotEqual(reservation_ids_1, reservation_ids_2)

        # Verify both sets of reservations exist
        all_reservations = StockReservation.objects.filter(
            session_id=str(self.cart.uuid)
        )
        self.assertEqual(all_reservations.count(), 2)
