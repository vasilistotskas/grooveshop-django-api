from django.urls import reverse
from rest_framework.test import APITestCase

from blog.factories.tag import BlogTagFactory


class BlogTagFilterTest(APITestCase):
    def setUp(self):
        self.tag1 = BlogTagFactory()
        self.tag2 = BlogTagFactory()

    def test_active_filter(self):
        url = reverse("blog-tag-list")

        response = self.client.get(url, {"active": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"active": "false"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_name_filters(self):
        url = reverse("blog-tag-list")

        response = self.client.get(url, {"name__icontains": "test"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"name__icontains": "tag"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"name__istartswith": "t"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_post_count_filters(self):
        url = reverse("blog-tag-list")

        response = self.client.get(url, {"has_posts": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"min_posts": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"max_posts": 10})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_post_relationship_filters(self):
        url = reverse("blog-tag-list")

        response = self.client.get(url, {"post__author": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"post__category": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"post__is_published": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_engagement_filters(self):
        url = reverse("blog-tag-list")

        response = self.client.get(url, {"has_engagement": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"min_engagement": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_timestamp_filters(self):
        url = reverse("blog-tag-list")

        response = self.client.get(
            url, {"created_after": "2024-01-01T00:00:00Z"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(
            url, {"created_before": "2025-12-31T23:59:59Z"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_uuid_and_sort_order_filters(self):
        url = reverse("blog-tag-list")

        response = self.client.get(url, {"uuid": str(self.tag1.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"sort_order__gte": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_camel_case_filters(self):
        url = reverse("blog-tag-list")

        response = self.client.get(url, {"isActive": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"hasPosts": "false"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_complex_filter_combinations(self):
        url = reverse("blog-tag-list")

        response = self.client.get(
            url, {"active": "true", "name__icontains": "T", "has_posts": "true"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"min_posts": 1, "active": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_special_filters(self):
        url = reverse("blog-tag-list")

        response = self.client.get(url, {"most_used": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"unused": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_filter_with_ordering(self):
        url = reverse("blog-tag-list")

        response = self.client.get(
            url, {"active": "true", "ordering": "sort_order"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)
