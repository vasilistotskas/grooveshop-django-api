from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from country.factories import CountryFactory
from country.models import Country
from order.enum.status_enum import OrderStatusEnum
from order.models.order import Order
from pay_way.factories import PayWayFactory
from pay_way.models import PayWay
from product.factories.product import ProductFactory
from product.models.product import Product
from region.factories import RegionFactory
from region.models import Region
from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum
from user.factories.account import UserAccountFactory

User = get_user_model()


class CheckoutViewAPITest(APITestCase):
    order: Order = None
    pay_way: PayWay = None
    country: Country = None
    region: Region = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.pay_way = PayWayFactory()
        self.country = CountryFactory(num_regions=0)
        self.region = RegionFactory(
            country=self.country,
        )

    @staticmethod
    def get_checkout_url():
        return reverse("checkout")

    def test_successful_order_creation(self):
        self.client.force_authenticate(user=self.user)
        products = ProductFactory.create_batch(2, num_images=0, num_reviews=0)

        product_1 = products[0]
        product_1.stock = 10
        product_1.save()

        product_2 = products[1]
        product_2.stock = 15
        product_2.save()

        order_data = {
            "user": self.user.id,
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "floor": FloorChoicesEnum.FIRST_FLOOR.value,
            "location_type": LocationChoicesEnum.HOME.value,
            "street": "123 Main St",
            "street_number": "Apt 4B",
            "pay_way": self.pay_way.id,
            "status": OrderStatusEnum.PENDING.value,
            "first_name": "John",
            "last_name": "Doe",
            "email": "test@test.com",
            "zipcode": "12345",
            "city": "Cityville",
            "phone": "2101234567",
            "mobile_phone": "6912345678",
            "customer_notes": "Test notes",
            "shipping_price": 10.00,
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
        self.assertEqual(Product.objects.get(pk=product_1.id).stock, 8)
        self.assertEqual(Product.objects.get(pk=product_2.id).stock, 12)

    def test_failed_order_creation(self):
        self.client.force_authenticate(user=self.user)
        products = ProductFactory.create_batch(2, num_images=0, num_reviews=0)

        product_3 = products[0]
        product_3.stock = 10
        product_3.save()

        product_4 = products[1]
        product_4.stock = 15
        product_4.save()

        order_data = {
            "user_id": self.user.id,
            "items": [
                {"product": product_3.id, "quantity": 15},
                {"product": product_4.id, "quantity": 3},
            ],
            "pay_way": self.pay_way.id,
        }

        url = self.get_checkout_url()
        response = self.client.post(url, data=order_data, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Product.objects.get(pk=product_3.id).stock, 10)
        self.assertEqual(Product.objects.get(pk=product_4.id).stock, 15)
