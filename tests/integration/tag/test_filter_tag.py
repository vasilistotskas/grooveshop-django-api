from django.urls import reverse
from rest_framework.test import APITestCase

from tag.factories.tag import TagFactory
from tag.factories.tagged_item import TaggedProductFactory
from product.factories.product import ProductFactory


class TagFilterTest(APITestCase):
    def setUp(self):
        self.tag1 = TagFactory(active=True)
        self.tag2 = TagFactory(active=False)
        self.product = ProductFactory()

        TaggedProductFactory(tag=self.tag1, content_object=self.product)

    def test_active_filter(self):
        url = reverse("tag-list")

        response = self.client.get(url, {"active": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        for tag_data in response.data["results"]:
            self.assertTrue(tag_data["active"])

        response = self.client.get(url, {"active": "false"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        for tag_data in response.data["results"]:
            self.assertFalse(tag_data["active"])

    def test_label_filters(self):
        url = reverse("tag-list")

        response = self.client.get(url, {"label__icontains": "test"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"label__icontains": "tag"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"label__istartswith": "t"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_usage_count_filters(self):
        url = reverse("tag-list")

        response = self.client.get(url, {"has_usage": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"min_usage_count": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"max_usage_count": 10})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_content_type_filters(self):
        url = reverse("tag-list")

        response = self.client.get(url, {"content_type": "product"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"content_type__app_label": "product"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"object_id": self.product.id})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_timestamp_filters(self):
        url = reverse("tag-list")

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
        url = reverse("tag-list")

        response = self.client.get(url, {"uuid": str(self.tag1.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"sort_order__gte": 1})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_camel_case_filters(self):
        url = reverse("tag-list")

        response = self.client.get(url, {"isActive": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"hasUsage": "false"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_complex_filter_combinations(self):
        url = reverse("tag-list")

        response = self.client.get(
            url,
            {"active": "true", "label__icontains": "T", "has_usage": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(
            url, {"min_usage_count": 1, "active": "true"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_special_filters(self):
        url = reverse("tag-list")

        response = self.client.get(url, {"most_used": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"unused": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_filter_with_ordering(self):
        url = reverse("tag-list")

        response = self.client.get(
            url, {"active": "true", "ordering": "sort_order"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_has_label_filter(self):
        tag_without_label = TagFactory(active=True)
        tag_without_label.translations.all().delete()

        url = reverse("tag-list")

        response = self.client.get(url, {"has_label": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"has_label": "false"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)
