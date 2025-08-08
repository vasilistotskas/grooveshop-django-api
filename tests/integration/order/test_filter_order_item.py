from django.contrib.auth import get_user_model
from django.urls import reverse
from datetime import timedelta
from django.utils import timezone
from rest_framework.test import APITestCase
from djmoney.money import Money

from order.models.item import OrderItem
from order.models.order import Order
from order.enum.status import OrderStatus, PaymentStatus
from user.factories.account import UserAccountFactory
from product.factories.product import ProductFactory
from product.factories.category import ProductCategoryFactory
from country.factories import CountryFactory
from region.factories import RegionFactory
from pay_way.factories import PayWayFactory

User = get_user_model()


class OrderItemFilterTest(APITestCase):
    def setUp(self):
        OrderItem.objects.all().delete()

        self.admin_user = UserAccountFactory(
            email="admin@example.com",
            first_name="Admin",
            last_name="User",
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(user=self.admin_user)

        self.user1 = UserAccountFactory(
            email="john.doe@example.com", first_name="John", last_name="Doe"
        )
        self.user2 = UserAccountFactory(
            email="jane.smith@example.com", first_name="Jane", last_name="Smith"
        )

        self.category1 = ProductCategoryFactory()
        self.category2 = ProductCategoryFactory()

        self.product1 = ProductFactory(
            sku="PROD001",
            category=self.category1,
            active=True,
            price=Money(25, "EUR"),
        )
        self.product2 = ProductFactory(
            sku="PROD002",
            category=self.category1,
            active=True,
            price=Money(50, "EUR"),
        )
        self.product3 = ProductFactory(
            sku="PROD003",
            category=self.category2,
            active=False,
            price=Money(75, "EUR"),
        )

        self.now = timezone.now()

        country = CountryFactory()
        region = RegionFactory()
        pay_way = PayWayFactory()

        self.order1 = Order.objects.create(
            user=self.user1,
            status=OrderStatus.PENDING,
            payment_status=PaymentStatus.PENDING,
            first_name="John",
            last_name="Doe",
            email="john.doe@example.com",
            country=country,
            region=region,
            pay_way=pay_way,
            street="123 Main St",
            street_number="123",
            city="Test City",
            zipcode="12345",
            shipping_price=Money(10, "EUR"),
            paid_amount=Money(0, "EUR"),
        )
        Order.objects.filter(id=self.order1.id).update(
            created_at=self.now - timedelta(hours=1)
        )

        self.order2 = Order.objects.create(
            user=self.user1,
            status=OrderStatus.COMPLETED,
            payment_status=PaymentStatus.COMPLETED,
            first_name="John",
            last_name="Doe",
            email="john.doe@example.com",
            country=country,
            region=region,
            pay_way=pay_way,
            street="123 Main St",
            street_number="123",
            city="Test City",
            zipcode="12345",
            shipping_price=Money(10, "EUR"),
            paid_amount=Money(100, "EUR"),
        )
        Order.objects.filter(id=self.order2.id).update(
            created_at=self.now - timedelta(days=2)
        )

        self.order3 = Order.objects.create(
            user=self.user2,
            status=OrderStatus.SHIPPED,
            payment_status=PaymentStatus.COMPLETED,
            first_name="Jane",
            last_name="Smith",
            email="jane.smith@example.com",
            country=country,
            region=region,
            pay_way=pay_way,
            street="456 Oak Ave",
            street_number="456",
            city="Test City",
            zipcode="54321",
            shipping_price=Money(15, "EUR"),
            paid_amount=Money(200, "EUR"),
        )
        Order.objects.filter(id=self.order3.id).update(
            created_at=self.now - timedelta(days=1)
        )

        self.item1 = OrderItem.objects.create(
            order=self.order1,
            product=self.product1,
            price=Money(25, "EUR"),
            quantity=2,
            original_quantity=2,
            is_refunded=False,
            refunded_quantity=0,
            notes="Standard item",
        )
        OrderItem.objects.filter(id=self.item1.id).update(
            created_at=self.now - timedelta(hours=1)
        )

        self.item2 = OrderItem.objects.create(
            order=self.order1,
            product=self.product2,
            price=Money(50, "EUR"),
            quantity=1,
            original_quantity=1,
            is_refunded=False,
            refunded_quantity=0,
            notes="",
        )
        OrderItem.objects.filter(id=self.item2.id).update(
            created_at=self.now - timedelta(hours=1)
        )

        self.item3 = OrderItem.objects.create(
            order=self.order2,
            product=self.product1,
            price=Money(25, "EUR"),
            quantity=10,
            original_quantity=10,
            is_refunded=True,
            refunded_quantity=10,
            notes="Customer requested refund",
        )
        OrderItem.objects.filter(id=self.item3.id).update(
            created_at=self.now - timedelta(days=2)
        )

        self.item4 = OrderItem.objects.create(
            order=self.order2,
            product=self.product2,
            price=Money(50, "EUR"),
            quantity=4,
            original_quantity=4,
            is_refunded=False,
            refunded_quantity=2,
            notes="Partial refund processed",
        )
        OrderItem.objects.filter(id=self.item4.id).update(
            created_at=self.now - timedelta(days=2)
        )

        self.item5 = OrderItem.objects.create(
            order=self.order3,
            product=self.product3,
            price=Money(150, "EUR"),
            quantity=1,
            original_quantity=1,
            is_refunded=False,
            refunded_quantity=0,
            notes="",
        )
        OrderItem.objects.filter(id=self.item5.id).update(
            created_at=self.now - timedelta(days=1)
        )

        self.old_item = OrderItem.objects.create(
            order=self.order3,
            product=self.product1,
            price=Money(25, "EUR"),
            quantity=1,
            original_quantity=1,
            is_refunded=False,
            refunded_quantity=0,
            notes="Old item",
        )
        OrderItem.objects.filter(id=self.old_item.id).update(
            created_at=self.now - timedelta(days=10)
        )

    def test_basic_filters(self):
        url = reverse("order-item-list")

        response = self.client.get(url, {"order": self.order1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.item1.id, result_ids)
        self.assertIn(self.item2.id, result_ids)

        response = self.client.get(url, {"product": self.product1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.item1.id, result_ids)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.old_item.id, result_ids)

        response = self.client.get(url, {"is_refunded": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.item3.id, result_ids)

    def test_product_relationship_filters(self):
        url = reverse("order-item-list")

        product_name = self.product1.safe_translation_getter(
            "name", any_language=True
        )
        if product_name:
            response = self.client.get(url, {"product__name": product_name[:5]})
            self.assertEqual(response.status_code, 200)
            result_ids = [r["id"] for r in response.data["results"]]
            self.assertGreaterEqual(len(result_ids), 1)

        response = self.client.get(url, {"product__sku": "PROD001"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.item1.id, result_ids)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.old_item.id, result_ids)

        response = self.client.get(
            url, {"product__category": self.category1.id}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 5)
        self.assertNotIn(self.item5.id, result_ids)

        response = self.client.get(url, {"product__active": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.item5.id, result_ids)

    def test_order_relationship_filters(self):
        url = reverse("order-item-list")

        response = self.client.get(
            url, {"order__status": OrderStatus.PENDING.value}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.item1.id, result_ids)
        self.assertIn(self.item2.id, result_ids)

        response = self.client.get(
            url, {"order__payment_status": PaymentStatus.COMPLETED.value}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.item4.id, result_ids)
        self.assertIn(self.item5.id, result_ids)
        self.assertIn(self.old_item.id, result_ids)

        response = self.client.get(url, {"order__user": self.user1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertIn(self.item1.id, result_ids)
        self.assertIn(self.item2.id, result_ids)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.item4.id, result_ids)

        response = self.client.get(url, {"order__user__email": "jane"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.item5.id, result_ids)
        self.assertIn(self.old_item.id, result_ids)

        response = self.client.get(url, {"order__first_name": "john"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertIn(self.item1.id, result_ids)
        self.assertIn(self.item2.id, result_ids)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.item4.id, result_ids)

    def test_price_filters(self):
        url = reverse("order-item-list")

        response = self.client.get(url, {"price_min": "50"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.item2.id, result_ids)
        self.assertIn(self.item4.id, result_ids)
        self.assertIn(self.item5.id, result_ids)

        response = self.client.get(url, {"price_max": "50"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 5)
        self.assertNotIn(self.item5.id, result_ids)

        response = self.client.get(url, {"price_exact": "25"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.item1.id, result_ids)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.old_item.id, result_ids)

    def test_quantity_filters(self):
        url = reverse("order-item-list")

        response = self.client.get(url, {"quantity_min": "4"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.item4.id, result_ids)

        response = self.client.get(url, {"quantity_max": "2"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertIn(self.item1.id, result_ids)
        self.assertIn(self.item2.id, result_ids)
        self.assertIn(self.item5.id, result_ids)
        self.assertIn(self.old_item.id, result_ids)

        response = self.client.get(url, {"quantity_exact": "1"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 3)
        self.assertIn(self.item2.id, result_ids)
        self.assertIn(self.item5.id, result_ids)
        self.assertIn(self.old_item.id, result_ids)

        response = self.client.get(url, {"original_quantity_min": "4"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.item4.id, result_ids)

    def test_refund_filters(self):
        url = reverse("order-item-list")

        response = self.client.get(url, {"refunded_quantity_min": "1"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.item4.id, result_ids)

        response = self.client.get(url, {"has_refunded_quantity": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.item4.id, result_ids)

        response = self.client.get(url, {"has_refunded_quantity": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertIn(self.item1.id, result_ids)
        self.assertIn(self.item2.id, result_ids)
        self.assertIn(self.item5.id, result_ids)
        self.assertIn(self.old_item.id, result_ids)

        response = self.client.get(url, {"is_partially_refunded": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.item4.id, result_ids)

        response = self.client.get(url, {"is_fully_refunded": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.item3.id, result_ids)

    def test_notes_filters(self):
        url = reverse("order-item-list")

        response = self.client.get(url, {"has_notes": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertIn(self.item1.id, result_ids)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.item4.id, result_ids)
        self.assertIn(self.old_item.id, result_ids)

        response = self.client.get(url, {"has_notes": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.item2.id, result_ids)
        self.assertIn(self.item5.id, result_ids)

        response = self.client.get(url, {"notes": "refund"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.item4.id, result_ids)

    def test_special_filters(self):
        url = reverse("order-item-list")

        response = self.client.get(url, {"high_value_items": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.item5.id, result_ids)

        response = self.client.get(url, {"high_value_items": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 5)
        self.assertNotIn(self.item5.id, result_ids)

        response = self.client.get(url, {"bulk_items": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.item3.id, result_ids)

        response = self.client.get(url, {"bulk_items": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 5)
        self.assertNotIn(self.item3.id, result_ids)

        response = self.client.get(url, {"recent_items": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 5)
        self.assertNotIn(self.old_item.id, result_ids)

    def test_bulk_filters(self):
        url = reverse("order-item-list")

        order_ids = f"{self.order1.id},{self.order2.id}"
        response = self.client.get(url, {"order_ids": order_ids})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertIn(self.item1.id, result_ids)
        self.assertIn(self.item2.id, result_ids)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.item4.id, result_ids)

        product_ids = f"{self.product1.id},{self.product3.id}"
        response = self.client.get(url, {"product_ids": product_ids})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertIn(self.item1.id, result_ids)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.item5.id, result_ids)
        self.assertIn(self.old_item.id, result_ids)

        order_statuses = (
            f"{OrderStatus.PENDING.value},{OrderStatus.COMPLETED.value}"
        )
        response = self.client.get(url, {"order_statuses": order_statuses})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 4)
        self.assertIn(self.item1.id, result_ids)
        self.assertIn(self.item2.id, result_ids)
        self.assertIn(self.item3.id, result_ids)
        self.assertIn(self.item4.id, result_ids)

        response = self.client.get(url, {"order_ids": "invalid,ids"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 0)

    def test_uuid_filter(self):
        url = reverse("order-item-list")

        response = self.client.get(url, {"uuid": str(self.item1.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], self.item1.id)

    def test_timestamp_filters(self):
        url = reverse("order-item-list")

        created_after_date = self.now - timedelta(days=3)
        response = self.client.get(
            url, {"created_after": created_after_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 5)
        self.assertNotIn(self.old_item.id, result_ids)

        created_before_date = self.now - timedelta(days=5)
        response = self.client.get(
            url, {"created_before": created_before_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.old_item.id, result_ids)

    def test_camel_case_filters(self):
        url = reverse("order-item-list")

        response = self.client.get(
            url,
            {
                "product__sku": "PROD001",
                "order__status": OrderStatus.PENDING.value,
                "has_notes": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.item1.id, result_ids)

    def test_complex_filter_combinations(self):
        url = reverse("order-item-list")

        response = self.client.get(
            url,
            {
                "order__status": OrderStatus.COMPLETED.value,
                "price_min": "50",
                "has_refunded_quantity": "false",
                "ordering": "-price",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 0)

        response = self.client.get(
            url,
            {
                "has_refunded_quantity": "true",
                "has_notes": "true",
                "order__payment_status": PaymentStatus.COMPLETED.value,
                "ordering": "-refunded_quantity",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)

        results = response.data["results"]

        result_ids = {r["id"] for r in results}
        expected_ids = {self.item3.id, self.item4.id}
        self.assertEqual(result_ids, expected_ids)

    def test_filter_with_ordering(self):
        url = reverse("order-item-list")

        response = self.client.get(
            url, {"order__user": self.user1.id, "ordering": "-price"}
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertEqual(len(results), 4)

        user1_items = [self.item1, self.item2, self.item3, self.item4]
        items_by_price_desc = [(item, item.price) for item in user1_items]
        items_by_price_desc.sort(key=lambda x: x[1].amount, reverse=True)

        high_price_ids = {
            items_by_price_desc[0][0].id,
            items_by_price_desc[1][0].id,
        }
        self.assertIn(results[0]["id"], high_price_ids)
        self.assertIn(results[1]["id"], high_price_ids)
        self.assertNotEqual(results[0]["id"], results[1]["id"])

        response = self.client.get(
            url, {"product": self.product1.id, "ordering": "-quantity"}
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertEqual(len(results), 3)

        items_for_product1 = [
            self.item1,
            self.item3,
            self.old_item,
        ]
        items_by_quantity_desc = [
            (item, item.quantity) for item in items_for_product1
        ]
        items_by_quantity_desc.sort(key=lambda x: x[1], reverse=True)

        self.assertEqual(results[0]["id"], items_by_quantity_desc[0][0].id)

    def tearDown(self):
        OrderItem.objects.all().delete()
