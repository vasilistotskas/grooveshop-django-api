from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from country.models import Country
from helpers.seed import get_or_create_default_image
from order.enum.status_enum import OrderStatusEnum
from order.models.order import Order
from pay_way.models import PayWay
from product.models.product import Product
from region.models import Region
from user.enum.address import FloorChoicesEnum
from user.enum.address import LocationChoicesEnum

User = get_user_model()


class CheckoutViewAPITest(APITestCase):
    order: Order = None
    pay_way: PayWay = None
    country: Country = None
    region: Region = None

    def setUp(self):
        # Create a sample user for testing
        self.user = User.objects.create_user(
            email="test@test.com", password="test12345@!"
        )

        # Login to authenticate
        self.client.login(email="test@test.com", password="test12345@!")

        # Create a sample PayWay instance for testing
        image_icon = get_or_create_default_image("uploads/pay_way/no_photo.jpg")
        self.pay_way = PayWay.objects.create(
            active=True,
            cost=10.00,
            free_for_order_amount=100.00,
            icon=image_icon,
        )

        # Create a sample Country instance for testing
        image_flag = get_or_create_default_image("uploads/region/no_photo.jpg")
        self.country = Country.objects.create(
            alpha_2="GR",
            alpha_3="GRC",
            iso_cc=300,
            phone_code=30,
            image_flag=image_flag,
        )

        # Create a sample Region instance for testing
        self.region = Region.objects.create(
            alpha="GRC",
            alpha_2=self.country,
        )

    @staticmethod
    def get_checkout_url():
        return reverse("checkout")

    def test_successful_order_creation(self):
        self.client.login(email=self.user.email, password="test12345@!")

        # Create products for testing
        product_1 = Product.objects.create(
            slug="product_one",
            price=10.00,
            active=True,
            stock=10,
            discount_percent=5.00,
            hits=0,
            weight=0.00,
        )
        product_2 = Product.objects.create(
            slug="product_two",
            price=25.00,
            active=True,
            stock=15,
            discount_percent=10.00,
            hits=0,
            weight=0.00,
        )

        # Prepare data for order creation
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
            "order_item_order": [
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
        self.client.login(email=self.user.email, password="test12345@!")

        # Create products for testing
        product_3 = Product.objects.create(
            slug="product_three",
            price=10.00,
            active=True,
            stock=10,
            discount_percent=5.00,
            hits=0,
            weight=0.00,
        )
        product_4 = Product.objects.create(
            slug="product_four",
            price=25.00,
            active=True,
            stock=15,
            discount_percent=10.00,
            hits=0,
            weight=0.00,
        )

        # Prepare data for order creation (invalid quantity to trigger a failure)
        order_data = {
            "user_id": self.user.id,
            "order_item_order": [
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

    def tearDown(self) -> None:
        super().tearDown()
        self.user.delete()
        self.pay_way.delete()
        self.country.delete()
        self.region.delete()
