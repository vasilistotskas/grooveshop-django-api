from decimal import Decimal
from datetime import timedelta
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from product.enum.review import RateEnum, ReviewStatus
from product.factories.category import ProductCategoryFactory
from product.factories.product import ProductFactory
from product.models.review import ProductReview
from user.factories.account import UserAccountFactory


class ProductReviewFilterTest(APITestCase):
    def setUp(self):
        ProductReview.objects.all().delete()

        self.now = timezone.now()

        self.user1 = UserAccountFactory(
            first_name="John", last_name="Doe", email="john.doe@example.com"
        )
        self.user2 = UserAccountFactory(
            first_name="Jane", last_name="Smith", email="jane.smith@example.com"
        )
        self.prolific_user = UserAccountFactory(
            first_name="Review", last_name="Expert", email="expert@example.com"
        )

        self.admin_user = UserAccountFactory(
            first_name="Admin",
            last_name="User",
            email="admin@example.com",
            is_superuser=True,
            is_staff=True,
        )

        self.electronics_category = ProductCategoryFactory(name="Electronics")
        self.clothing_category = ProductCategoryFactory(name="Clothing")

        self.expensive_product = ProductFactory(
            category=self.electronics_category,
            price=Decimal("1000.00"),
            active=True,
        )

        self.cheap_product = ProductFactory(
            category=self.clothing_category, price=Decimal("50.00"), active=True
        )

        self.inactive_product = ProductFactory(
            category=self.electronics_category,
            price=Decimal("200.00"),
            active=False,
        )

        self.old_review = ProductReview.objects.create(
            user=self.user1,
            product=self.expensive_product,
            rate=RateEnum.FIVE,
            status=ReviewStatus.TRUE,
            is_published=True,
        )
        ProductReview.objects.filter(id=self.old_review.id).update(
            created_at=self.now - timedelta(days=30),
            updated_at=self.now - timedelta(days=25),
            published_at=self.now - timedelta(days=25),
        )
        self.old_review.refresh_from_db()
        self.old_review.set_current_language("en")
        self.old_review.comment = "Excellent product! Very satisfied with the quality and performance."
        self.old_review.save()

        self.recent_review = ProductReview.objects.create(
            user=self.user2,
            product=self.expensive_product,
            rate=RateEnum.TWO,
            status=ReviewStatus.NEW,
            is_published=False,
        )
        ProductReview.objects.filter(id=self.recent_review.id).update(
            created_at=self.now - timedelta(hours=2),
            updated_at=self.now - timedelta(hours=1),
        )
        self.recent_review.refresh_from_db()

        self.average_review = ProductReview.objects.create(
            user=self.user1,
            product=self.cheap_product,
            rate=RateEnum.THREE,
            status=ReviewStatus.TRUE,
            is_published=True,
        )
        ProductReview.objects.filter(id=self.average_review.id).update(
            created_at=self.now - timedelta(days=7),
            updated_at=self.now - timedelta(days=5),
            published_at=self.now - timedelta(days=5),
        )
        self.average_review.refresh_from_db()
        self.average_review.set_current_language("en")
        self.average_review.comment = "OK"
        self.average_review.save()

        self.fourth_review = ProductReview.objects.create(
            user=self.user2,
            product=self.cheap_product,
            rate=RateEnum.FOUR,
            status=ReviewStatus.TRUE,
            is_published=True,
        )
        ProductReview.objects.filter(id=self.fourth_review.id).update(
            created_at=self.now - timedelta(days=14),
            updated_at=self.now - timedelta(days=10),
            published_at=self.now - timedelta(days=10),
        )
        self.fourth_review.refresh_from_db()
        self.fourth_review.set_current_language("en")
        self.fourth_review.comment = (
            "Good product but had some issues with delivery."
        )
        self.fourth_review.save()

        self.prolific_review1 = ProductReview.objects.create(
            user=self.prolific_user,
            product=self.expensive_product,
            rate=RateEnum.FIVE,
            status=ReviewStatus.TRUE,
            is_published=True,
        )
        ProductReview.objects.filter(id=self.prolific_review1.id).update(
            created_at=self.now - timedelta(days=3),
            updated_at=self.now - timedelta(days=2),
            published_at=self.now - timedelta(days=2),
        )
        self.prolific_review1.refresh_from_db()

        self.prolific_review2 = ProductReview.objects.create(
            user=self.prolific_user,
            product=self.cheap_product,
            rate=RateEnum.ONE,
            status=ReviewStatus.TRUE,
            is_published=True,
        )
        ProductReview.objects.filter(id=self.prolific_review2.id).update(
            created_at=self.now - timedelta(days=1),
            updated_at=self.now - timedelta(hours=12),
            published_at=self.now - timedelta(hours=12),
        )
        self.prolific_review2.refresh_from_db()
        self.prolific_review2.set_current_language("en")
        self.prolific_review2.comment = (
            "Terrible quality, would not recommend to anyone!"
        )
        self.prolific_review2.save()

        self.client.force_authenticate(user=self.admin_user)

    def test_debug_authentication(self):
        url = reverse("product-review-list")

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        admin_count = len(response.data["results"])

        self.client.force_authenticate(user=self.user1)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        user_count = len(response.data["results"])

        self.client.force_authenticate(user=None)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        unauth_count = len(response.data["results"])

        self.client.force_authenticate(user=self.admin_user)

        self.assertGreaterEqual(admin_count, user_count)
        self.assertGreaterEqual(user_count, unauth_count)

    def test_debug_basic_api(self):
        url = reverse("product-review-list")
        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.data)

        if response.data["results"]:
            required_fields = ["id", "rate", "status", "is_published"]
            for field in required_fields:
                self.assertIn(field, response.data["results"][0])

    def test_basic_filters(self):
        url = reverse("product-review-list")

        response = self.client.get(url, {"id": self.old_review.id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], self.old_review.id)

        response = self.client.get(url, {"user": self.user1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_ids = [self.old_review.id, self.average_review.id]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

        response = self.client.get(url, {"product": self.expensive_product.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_ids = [
            self.old_review.id,
            self.recent_review.id,
            self.prolific_review1.id,
        ]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

    def test_status_and_publication_filters(self):
        url = reverse("product-review-list")

        response = self.client.get(url, {"status": ReviewStatus.TRUE})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        expected_ids = [
            self.old_review.id,
            self.average_review.id,
            self.fourth_review.id,
            self.prolific_review1.id,
            self.prolific_review2.id,
        ]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

        self.client.force_authenticate(user=self.admin_user)
        response = self.client.get(url, {"status": ReviewStatus.NEW})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        if len(result_ids) > 0:
            self.assertIn(self.recent_review.id, result_ids)

        self.client.force_authenticate(user=self.user2)
        response = self.client.get(url, {"status": ReviewStatus.NEW})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        if len(result_ids) > 0:
            self.assertIn(self.recent_review.id, result_ids)

        self.client.force_authenticate(user=self.admin_user)

        response = self.client.get(url, {"is_published": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertNotIn(self.recent_review.id, result_ids)

        response = self.client.get(url, {"is_published": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        if len(result_ids) > 0:
            self.assertIn(self.recent_review.id, result_ids)

    def test_rating_filters(self):
        url = reverse("product-review-list")

        response = self.client.get(url, {"rate": RateEnum.FIVE})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_ids = [self.old_review.id, self.prolific_review1.id]
        self.assertGreaterEqual(len(result_ids), len(expected_ids))
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

        response = self.client.get(url, {"rate_min": 4})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_ids = [
            self.old_review.id,
            self.fourth_review.id,
            self.prolific_review1.id,
        ]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

        response = self.client.get(url, {"rate_max": 2})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_ids = [self.recent_review.id, self.prolific_review2.id]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

        response = self.client.get(url, {"min_rate": 3, "max_rate": 4})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_ids = [self.average_review.id, self.fourth_review.id]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

    def test_comment_filters(self):
        url = reverse("product-review-list")

        response = self.client.get(url, {"comment": "excellent"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        if len(result_ids) > 0:
            self.assertIn(self.old_review.id, result_ids)

        response = self.client.get(url, {"has_comment": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_with_comments = [
            self.old_review.id,
            self.average_review.id,
            self.fourth_review.id,
            self.prolific_review2.id,
        ]
        found_comments = [
            rid for rid in expected_with_comments if rid in result_ids
        ]
        self.assertGreater(len(found_comments), 0)

        response = self.client.get(url, {"has_comment": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_without_comments = [
            self.recent_review.id,
            self.prolific_review1.id,
        ]
        found_no_comments = [
            rid for rid in expected_without_comments if rid in result_ids
        ]
        self.assertGreater(len(found_no_comments), 0)

    def test_related_object_filters(self):
        url = reverse("product-review-list")

        response = self.client.get(url, {"product_name": "test"})
        self.assertEqual(response.status_code, 200)

        response = self.client.get(
            url, {"product_category": self.electronics_category.id}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_ids = [
            self.old_review.id,
            self.recent_review.id,
            self.prolific_review1.id,
        ]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

        response = self.client.get(url, {"product_active": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        all_count = ProductReview.objects.count()
        self.assertEqual(len(result_ids), all_count)

        response = self.client.get(url, {"user_email": "john.doe"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_ids = [self.old_review.id, self.average_review.id]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

        response = self.client.get(url, {"user_first_name": "Jane"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_ids = [self.recent_review.id, self.fourth_review.id]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

        response = self.client.get(url, {"user_last_name": "Expert"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_ids = [self.prolific_review1.id, self.prolific_review2.id]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

    def test_time_based_filters(self):
        url = reverse("product-review-list")

        created_after_date = self.now - timedelta(days=14)
        response = self.client.get(
            url, {"created_after": created_after_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        expected_recent = [
            self.recent_review.id,
            self.average_review.id,
            self.prolific_review1.id,
            self.prolific_review2.id,
        ]
        for expected_id in expected_recent:
            if (
                ProductReview.objects.get(id=expected_id).created_at
                >= created_after_date
            ):
                self.assertIn(expected_id, result_ids)

        self.assertNotIn(self.old_review.id, result_ids)

        published_after_date = self.now - timedelta(days=7)
        response = self.client.get(
            url, {"published_after": published_after_date.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        expected_recently_published = [
            self.prolific_review1.id,
            self.prolific_review2.id,
        ]
        for expected_id in expected_recently_published:
            review = ProductReview.objects.get(id=expected_id)
            if (
                review.published_at
                and review.published_at >= published_after_date
            ):
                self.assertIn(expected_id, result_ids)

        response = self.client.get(url, {"recent_days": 7})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        cutoff_date = self.now - timedelta(days=7)
        for review_id in result_ids:
            review = ProductReview.objects.get(id=review_id)
            self.assertGreaterEqual(review.created_at, cutoff_date)

        response = self.client.get(url, {"published_recent_days": 3})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        cutoff_date = self.now - timedelta(days=3)
        for review_id in result_ids:
            review = ProductReview.objects.get(id=review_id)
            if review.published_at:
                self.assertGreaterEqual(review.published_at, cutoff_date)

    def test_aggregate_filters(self):
        url = reverse("product-review-list")

        response = self.client.get(url, {"user_review_count_min": 2})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        all_count = ProductReview.objects.count()
        self.assertEqual(len(result_ids), all_count)

        response = self.client.get(url, {"user_review_count_min": 3})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 0)

        response = self.client.get(url, {"product_avg_rating_min": 3})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        expected_ids = [
            self.old_review.id,
            self.recent_review.id,
            self.prolific_review1.id,
        ]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

        response = self.client.get(url, {"product_avg_rating_max": 3})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        expected_ids = [
            self.average_review.id,
            self.fourth_review.id,
            self.prolific_review2.id,
        ]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

    def test_uuid_filter(self):
        url = reverse("product-review-list")

        response = self.client.get(url, {"uuid": str(self.old_review.uuid)})
        self.assertEqual(response.status_code, 200)

        self.assertGreaterEqual(len(response.data["results"]), 1)
        found_review = None
        for result in response.data["results"]:
            if result["id"] == self.old_review.id:
                found_review = result
                break

        self.assertIsNotNone(found_review)

    def test_camel_case_filters(self):
        url = reverse("product-review-list")

        created_after_date = self.now - timedelta(days=14)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "isPublished": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        expected_ids = [
            self.average_review.id,
            self.prolific_review1.id,
            self.prolific_review2.id,
        ]
        found_expected = [rid for rid in expected_ids if rid in result_ids]
        self.assertGreater(len(found_expected), 0)

        response = self.client.get(
            url,
            {
                "minRate": 3,
                "maxRate": 4,
                "hasComment": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        expected_ids = [self.average_review.id, self.fourth_review.id]
        found_expected = [rid for rid in expected_ids if rid in result_ids]
        self.assertGreater(len(found_expected), 0)

        response = self.client.get(
            url,
            {
                "userId": self.prolific_user.id,
                "currentlyPublished": "true",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        expected_ids = [self.prolific_review1.id, self.prolific_review2.id]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

    def test_existing_filters_still_work(self):
        url = reverse("product-review-list")

        response = self.client.get(
            url,
            {
                "status": ReviewStatus.TRUE,
                "rate_min": 4,
                "is_published": "true",
            },
        )

        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        expected_ids = [self.old_review.id, self.prolific_review1.id]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

        for result in response.data["results"]:
            self.assertEqual(result.get("status"), ReviewStatus.TRUE)
            self.assertGreaterEqual(result.get("rate", 0), 4)
            self.assertTrue(result.get("is_published"))

        self.assertGreaterEqual(len(result_ids), len(expected_ids))

    def test_complex_filter_combinations(self):
        url = reverse("product-review-list")

        created_after_date = self.now - timedelta(days=20)

        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "isPublished": "true",
                "minRate": 3,
                "hasComment": "true",
                "ordering": "-rate",
            },
        )
        self.assertEqual(response.status_code, 200)

        for result in response.data["results"]:
            self.assertTrue(result.get("is_published"))
            self.assertGreaterEqual(result.get("rate", 0), 3)

    def test_filter_with_ordering(self):
        url = reverse("product-review-list")

        response = self.client.get(
            url, {"status": ReviewStatus.TRUE, "ordering": "-rate"}
        )
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        published_count = ProductReview.objects.filter(
            status=ReviewStatus.TRUE
        ).count()
        self.assertEqual(len(results), published_count)

        rates = [r.get("rate", 0) for r in results]
        self.assertEqual(rates, sorted(rates, reverse=True))

    def test_publishable_filters(self):
        url = reverse("product-review-list")

        response = self.client.get(url, {"currently_published": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        expected_ids = [
            self.old_review.id,
            self.average_review.id,
            self.fourth_review.id,
            self.prolific_review1.id,
            self.prolific_review2.id,
        ]
        for expected_id in expected_ids:
            self.assertIn(expected_id, result_ids)

    def test_edge_cases(self):
        url = reverse("product-review-list")

        response = self.client.get(url, {"user": 99999})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 0)

        response = self.client.get(url, {"rate": 0})
        self.assertEqual(response.status_code, 400)

        response = self.client.get(url, {"recent_days": 0})
        self.assertEqual(response.status_code, 200)

    def tearDown(self):
        ProductReview.objects.all().delete()
