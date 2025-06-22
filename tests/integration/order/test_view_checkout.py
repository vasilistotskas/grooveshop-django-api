from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from core.enum import FloorChoicesEnum, LocationChoicesEnum
from country.factories import CountryFactory
from order.enum.status import OrderStatus
from pay_way.factories import PayWayFactory
from product.factories.product import ProductFactory
from product.models.product import Product
from region.factories import RegionFactory
from user.factories.account import UserAccountFactory

User = get_user_model()


class CheckoutViewAPITest(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = UserAccountFactory(num_addresses=0)
        self.country = CountryFactory(num_regions=0)
        self.region = RegionFactory(country=self.country)
        self.pay_way = PayWayFactory()

    def get_checkout_url(self):
        return reverse("order-list")

    def test_successful_order_creation(self):
        self.client.force_authenticate(user=self.user)
        product_1 = ProductFactory.create(
            active=True, stock=20, num_images=0, num_reviews=0
        )
        product_2 = ProductFactory.create(
            active=True, stock=20, num_images=0, num_reviews=0
        )

        order_data = {
            "user": self.user.id,
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "floor": FloorChoicesEnum.FIRST_FLOOR.value,
            "location_type": LocationChoicesEnum.HOME.value,
            "street": "123 Main St",
            "street_number": "Apt 4B",
            "pay_way": self.pay_way.id,
            "status": OrderStatus.PENDING.value,
            "first_name": "John",
            "last_name": "Doe",
            "email": "test@test.com",
            "zipcode": "12345",
            "city": "Cityville",
            "phone": "2101234567",
            "mobile_phone": "6912345678",
            "customer_notes": "Test notes",
            "shipping_price": "10.00",
            "items": [
                {
                    "product": product_1.id,
                    "quantity": 2,
                },
                {
                    "product": product_2.id,
                    "quantity": 3,
                },
            ],
        }

        url = self.get_checkout_url()
        response = self.client.post(url, data=order_data, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        product_1.refresh_from_db()
        product_2.refresh_from_db()

        self.assertEqual(product_1.stock, 20 - 2)
        self.assertEqual(product_2.stock, 20 - 3)

    def test_failed_order_creation(self):
        self.client.force_authenticate(user=self.user)
        product_3 = ProductFactory.create(stock=10, num_images=0, num_reviews=0)
        product_4 = ProductFactory.create(stock=15, num_images=0, num_reviews=0)

        order_data = {
            "user_id": self.user.id,
            "items": [
                {
                    "product": product_3.id,
                    "quantity": 15,
                },
                {"product": product_4.id, "quantity": 3},
            ],
            "pay_way": self.pay_way.id,
        }

        url = self.get_checkout_url()
        response = self.client.post(url, data=order_data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Product.objects.get(pk=product_3.id).stock, 10)
        self.assertEqual(Product.objects.get(pk=product_4.id).stock, 15)
