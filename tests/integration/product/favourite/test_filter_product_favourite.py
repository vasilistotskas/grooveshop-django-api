from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from datetime import timedelta
from django.utils import timezone
from rest_framework.test import APITestCase
from product.factories.product import ProductFactory
from product.models.favourite import ProductFavourite
from user.factories.account import UserAccountFactory

User = get_user_model()


class ProductFavouriteFilterTest(APITestCase):
    def setUp(self):
        ProductFavourite.objects.all().delete()

        self.user1 = UserAccountFactory(first_name="John", last_name="Doe")
        self.user2 = UserAccountFactory(first_name="Jane", last_name="Smith")
        self.product1 = ProductFactory(price=Decimal("100.00"), active=True)
        self.product2 = ProductFactory(price=Decimal("50.00"), active=True)

        self.now = timezone.now()

        self.old_favourite = ProductFavourite.objects.create(
            user=self.user1,
            product=self.product1,
        )
        ProductFavourite.objects.filter(id=self.old_favourite.id).update(
            created_at=self.now - timedelta(days=60),
            updated_at=self.now - timedelta(days=50),
        )
        self.old_favourite.refresh_from_db()

        self.recent_favourite = ProductFavourite.objects.create(
            user=self.user1,
            product=self.product2,
        )
        ProductFavourite.objects.filter(id=self.recent_favourite.id).update(
            created_at=self.now - timedelta(hours=2),
            updated_at=self.now - timedelta(hours=1),
        )
        self.recent_favourite.refresh_from_db()

        self.user2_favourite = ProductFavourite.objects.create(
            user=self.user2,
            product=self.product1,
        )
        ProductFavourite.objects.filter(id=self.user2_favourite.id).update(
            created_at=self.now - timedelta(hours=3),
            updated_at=self.now - timedelta(hours=2),
        )
        self.user2_favourite.refresh_from_db()

        self.high_sort_favourite = ProductFavourite.objects.create(
            user=self.user2,
            product=self.product2,
        )
        ProductFavourite.objects.filter(id=self.high_sort_favourite.id).update(
            created_at=self.now - timedelta(hours=4),
            updated_at=self.now - timedelta(hours=3),
        )
        self.high_sort_favourite.refresh_from_db()

    def test_timestamp_filters(self):
        url = reverse("product-favourite-list")

        created_after_date = self.now - timedelta(days=30)
        response = self.client.get(
            url,
            {"created_after": created_after_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertNotIn(self.old_favourite.id, result_ids)

        created_before_date = self.now - timedelta(days=30)
        response = self.client.get(
            url,
            {"created_before": created_before_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        if self.old_favourite.created_at < created_before_date:
            self.assertIn(self.old_favourite.id, result_ids)

    def test_id_filters(self):
        url = reverse("product-favourite-list")

        response = self.client.get(url, {"id": self.recent_favourite.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.recent_favourite.id
        )

        response = self.client.get(url, {"product_id": self.product1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.old_favourite.id, result_ids)
        self.assertIn(self.user2_favourite.id, result_ids)

        response = self.client.get(url, {"user_id": self.user1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.old_favourite.id, result_ids)
        self.assertIn(self.recent_favourite.id, result_ids)

    def test_uuid_filter(self):
        url = reverse("product-favourite-list")

        response = self.client.get(
            url, {"uuid": str(self.recent_favourite.uuid)}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.recent_favourite.id
        )

    def test_sort_order_filters_if_available(self):
        url = reverse("product-favourite-list")

        has_sort_order = hasattr(ProductFavourite, "sort_order")

        if not has_sort_order:
            return

        response = self.client.get(url, {"sort_order": 200})
        self.assertEqual(response.status_code, 200)

    def test_camel_case_filters(self):
        url = reverse("product-favourite-list")

        created_after_date = self.now - timedelta(days=30)

        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "userId": self.user1.id,
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_existing_filters_still_work(self):
        url = reverse("product-favourite-list")

        response = self.client.get(
            url,
            {
                "user_id": self.user2.id,
                "product_id": self.product1.id,
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.user2_favourite.id, result_ids)

    def test_complex_filter_combinations(self):
        url = reverse("product-favourite-list")

        response = self.client.get(
            url,
            {
                "user_id": self.user1.id,
                "ordering": "-created_at",
            },
        )

        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.old_favourite.id, result_ids)
        self.assertIn(self.recent_favourite.id, result_ids)

    def test_filter_with_ordering(self):
        url = reverse("product-favourite-list")

        response = self.client.get(
            url, {"user_id": self.user1.id, "ordering": "-created_at"}
        )
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        self.assertEqual(len(results), 2)

        favourite_ids = [r["id"] for r in results]
        self.assertIn(self.old_favourite.id, favourite_ids)
        self.assertIn(self.recent_favourite.id, favourite_ids)

    def test_multiple_products_filter(self):
        url = reverse("product-favourite-list")

        response = self.client.get(
            url,
            {
                "product_id": self.product1.id,
                "ordering": "created_at",
            },
        )
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        self.assertEqual(len(results), 2)

        result_ids = [r["id"] for r in results]
        self.assertIn(self.old_favourite.id, result_ids)
        self.assertIn(self.user2_favourite.id, result_ids)

    def test_timestamp_range_filters(self):
        url = reverse("product-favourite-list")

        created_after = self.now - timedelta(days=7)

        response = self.client.get(
            url,
            {
                "created_after": created_after.isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]

        self.assertNotIn(self.old_favourite.id, result_ids)

    def tearDown(self):
        ProductFavourite.objects.all().delete()
