from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from djmoney.money import Money
from rest_framework import status
from rest_framework.test import APITestCase

from core.enum import FloorChoicesEnum, LocationChoicesEnum
from country.factories import CountryFactory
from country.models import Country
from order.enum.status import OrderStatusEnum
from order.factories.order import OrderFactory
from order.models.item import OrderItem
from order.models.order import Order
from order.serializers.order import OrderDetailSerializer, OrderSerializer
from pay_way.factories import PayWayFactory
from pay_way.models import PayWay
from product.factories.product import ProductFactory
from region.factories import RegionFactory
from region.models import Region

User = get_user_model()


class OrderViewSetTestCase(APITestCase):
    order: Order = None
    pay_way: PayWay = None
    country: Country = None
    region: Region = None
    order_items: list[OrderItem] = None

    def setUp(self):
        super().setUp()

        self.user = User.objects.create_user(
            username="testuser",
            email="testuser@example.com",
            password="testpassword",
        )
        self.admin_user = User.objects.create_user(
            username="admin",
            email="admin@example.com",
            password="adminpassword",
            is_staff=True,
            is_superuser=True,
        )

        self.pay_way = PayWayFactory()
        self.country = CountryFactory()
        self.region = RegionFactory(country=self.country)

        self.order = OrderFactory(
            user=self.user,
            status=OrderStatusEnum.PENDING.value,
            pay_way=self.pay_way,
            country=self.country,
            region=self.region,
        )

        products = ProductFactory.create_batch(
            2, active=True, num_images=0, num_reviews=0, stock=20
        )
        self.order_items = []

        for i, product in enumerate(products):
            item = self.order.items.create(
                product=product,
                price=Money("50.00", settings.DEFAULT_CURRENCY),
                quantity=2 if i == 0 else 3,
            )
            self.order_items.append(item)

        self.client.force_authenticate(user=self.admin_user)

    @staticmethod
    def get_order_detail_url(order_id):
        return reverse("order-detail", kwargs={"pk": order_id})

    @staticmethod
    def get_order_list_url():
        return reverse("order-list")

    def test_list(self):
        self.client.force_authenticate(user=self.admin_user)
        url = self.get_order_list_url()
        response = self.client.get(url)
        orders = Order.objects.all()
        serializer = OrderSerializer(orders, many=True)
        self.assertEqual(response.data["results"], serializer.data)

    def test_create_valid(self):
        self.client.force_authenticate(user=self.admin_user)
        payload = {
            "user": self.user.id,
            "pay_way": self.pay_way.id,
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "floor": FloorChoicesEnum.FIRST_FLOOR.value,
            "location_type": LocationChoicesEnum.HOME.value,
            "email": "test@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "street": "123 Main St",
            "street_number": "Apt 4",
            "city": "New York",
            "zipcode": "10001",
            "phone": "2101234567",
            "mobile_phone": "6912345678",
            "paid_amount": Decimal("150.00"),
            "status": OrderStatusEnum.PENDING.value,
            "shipping_price": Decimal("10.00"),
            "items": [
                {
                    "product": self.order_items[0].product.id,
                    "quantity": 1,
                },
                {
                    "product": self.order_items[1].product.id,
                    "quantity": 1,
                },
            ],
        }

        url = self.get_order_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        self.client.force_authenticate(user=self.admin_user)
        payload = {
            "user": "invalid_user_id",
            "pay_way": "invalid_pay_way_id",
            "country": "invalid_country_id",
            "region": "invalid_region_id",
            "floor": "invalid_floor",
            "location_type": "invalid_location_type",
            "email": "invalid_email",
            "first_name": "invalid_first_name",
            "last_name": "invalid_last_name",
            "street": "invalid_street",
            "street_number": "invalid_street_number",
            "city": "invalid_city",
            "zipcode": "invalid_zipcode",
            "phone": "invalid_phone",
            "mobile_phone": "invalid_mobile_phone",
            "paid_amount": "invalid_paid_amount",
            "status": "invalid_status",
            "shipping_price": "invalid_shipping_price",
            "items": [
                {
                    "product": "invalid_product_id",
                    "quantity": "invalid_quantity",
                },
                {
                    "product": "invalid_product_id",
                    "quantity": "invalid_quantity",
                },
            ],
        }

        url = self.get_order_list_url()
        response = self.client.post(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        self.client.force_authenticate(user=self.admin_user)
        url = self.get_order_detail_url(self.order.id)
        response = self.client.get(url)
        order = Order.objects.get(id=self.order.id)
        serializer = OrderDetailSerializer(
            order, context={"request": response.wsgi_request}
        )
        self.assertEqual(response.data, serializer.data)

    def test_retrieve_invalid(self):
        self.client.force_authenticate(user=self.admin_user)
        invalid_order_id = 999999
        url = self.get_order_detail_url(invalid_order_id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        self.client.force_authenticate(user=self.admin_user)

        self.order.status = OrderStatusEnum.PROCESSING.value
        self.order.save()

        payload = {
            "user": self.user.id,
            "pay_way": self.pay_way.id,
            "country": self.country.alpha_2,
            "region": self.region.alpha,
            "floor": FloorChoicesEnum.FIRST_FLOOR.value,
            "location_type": LocationChoicesEnum.HOME.value,
            "email": "test@example.com",
            "first_name": "John",
            "last_name": "Doe",
            "street": "123 Main St",
            "street_number": "Apt 4",
            "city": "New York",
            "zipcode": "10001",
            "phone": "2101234567",
            "mobile_phone": "6912345678",
            "paid_amount": Decimal("150.00"),
            "status": OrderStatusEnum.SHIPPED.value,
            "shipping_price": Decimal("10.00"),
            "items": [
                {
                    "product": self.order_items[0].product.id,
                    "quantity": 1,
                },
                {
                    "product": self.order_items[1].product.id,
                    "quantity": 1,
                },
            ],
        }

        url = self.get_order_detail_url(self.order.id)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        self.client.force_authenticate(user=self.admin_user)
        payload = {
            "user": "invalid_user_id",
            "pay_way": "invalid_pay_way_id",
            "country": "invalid_country_id",
            "region": "invalid_region_id",
            "floor": "invalid_floor",
            "location_type": "invalid_location_type",
            "email": "invalid_email",
            "first_name": "invalid_first_name",
            "last_name": "invalid_last_name",
            "street": "invalid_street",
            "street_number": "invalid_street_number",
            "city": "invalid_city",
            "zipcode": "invalid_zipcode",
            "phone": "invalid_phone",
            "mobile_phone": "invalid_mobile_phone",
            "paid_amount": "invalid_paid_amount",
            "status": "invalid_status",
            "shipping_price": "invalid_shipping_price",
            "items": [
                {
                    "product": "invalid_product_id",
                    "quantity": "invalid_quantity",
                },
                {
                    "product": "invalid_product_id",
                    "quantity": "invalid_quantity",
                },
            ],
        }

        url = self.get_order_detail_url(self.order.id)
        response = self.client.put(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        self.client.force_authenticate(user=self.admin_user)
        payload = {
            "status": OrderStatusEnum.SHIPPED.value,
        }

        url = self.get_order_detail_url(self.order.id)
        response = self.client.patch(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        self.client.force_authenticate(user=self.admin_user)
        payload = {
            "status": "invalid_status",
        }

        url = self.get_order_detail_url(self.order.id)
        response = self.client.patch(url, data=payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        self.client.force_authenticate(user=self.admin_user)
        url = self.get_order_detail_url(self.order.id)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Order.objects.filter(id=self.order.id).exists())

    def test_destroy_invalid(self):
        self.client.force_authenticate(user=self.admin_user)
        invalid_order_id = 999999
        url = self.get_order_detail_url(invalid_order_id)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
