from django.urls import reverse
from rest_framework.test import APITestCase

from tag.factories.tag import TagFactory
from tag.factories.tagged_item import TaggedProductFactory
from product.factories.product import ProductFactory


class TaggedItemFilterTest(APITestCase):
    def setUp(self):
        self.tag1 = TagFactory(active=True)
        self.tag2 = TagFactory(active=False)
        self.product1 = ProductFactory()
        self.product2 = ProductFactory()

        self.tagged_item1 = TaggedProductFactory(
            tag=self.tag1, content_object=self.product1
        )
        self.tagged_item2 = TaggedProductFactory(
            tag=self.tag2, content_object=self.product2
        )

    def test_tag_filter(self):
        url = reverse("tagged-item-list")

        response = self.client.get(url, {"tag": self.tag1.id})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        for item_data in response.data["results"]:
            self.assertEqual(item_data["tag"]["id"], self.tag1.id)

    def test_tag_label_filter(self):
        url = reverse("tagged-item-list")

        response = self.client.get(url, {"tag__label__icontains": "test"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_tag_active_filter(self):
        url = reverse("tagged-item-list")

        response = self.client.get(url, {"tag__active": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        for item_data in response.data["results"]:
            self.assertTrue(item_data["tag"]["active"])

        response = self.client.get(url, {"tag__active": "false"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        for item_data in response.data["results"]:
            self.assertFalse(item_data["tag"]["active"])

    def test_content_type_filter(self):
        url = reverse("tagged-item-list")

        response = self.client.get(url, {"content_type": "product"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        for item_data in response.data["results"]:
            self.assertEqual(item_data["content_type_name"], "product")

    def test_content_type_app_label_filter(self):
        url = reverse("tagged-item-list")

        response = self.client.get(url, {"content_type__app_label": "product"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_object_id_filter(self):
        url = reverse("tagged-item-list")

        response = self.client.get(url, {"object_id": self.product1.id})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        for item_data in response.data["results"]:
            self.assertEqual(item_data["object_id"], self.product1.id)

    def test_object_id_in_filter(self):
        url = reverse("tagged-item-list")

        object_ids = f"{self.product1.id},{self.product2.id}"
        response = self.client.get(url, {"object_id__in": object_ids})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_timestamp_filters(self):
        url = reverse("tagged-item-list")

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

    def test_uuid_filter(self):
        url = reverse("tagged-item-list")

        response = self.client.get(url, {"uuid": str(self.tagged_item1.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_complex_filter_combinations(self):
        url = reverse("tagged-item-list")

        response = self.client.get(
            url,
            {
                "tag__active": "true",
                "content_type": "product",
                "object_id": self.product1.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_filter_with_ordering(self):
        url = reverse("tagged-item-list")

        response = self.client.get(
            url, {"tag__active": "true", "ordering": "created_at"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)
