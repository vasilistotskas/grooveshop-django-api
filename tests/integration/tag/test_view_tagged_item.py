from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from tag.factories.tag import TagFactory
from tag.factories.tagged_item import TaggedProductFactory
from tag.models.tagged_item import TaggedItem
from product.factories.product import ProductFactory
from core.utils.testing import TestURLFixerMixin


class TaggedItemViewSetTestCase(TestURLFixerMixin, APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tag = TagFactory(active=True)
        cls.inactive_tag = TagFactory(active=False)
        cls.product = ProductFactory()
        cls.tagged_item = TaggedProductFactory(
            tag=cls.tag, content_object=cls.product
        )

    def get_tagged_item_detail_url(self, pk):
        return reverse("tagged-item-detail", args=[pk])

    def get_tagged_item_list_url(self):
        return reverse("tagged-item-list")

    def test_list(self):
        url = self.get_tagged_item_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIn("count", response.data)
        if "links" in response.data:
            self.assertIn("next", response.data["links"])
            self.assertIn("previous", response.data["links"])
        else:
            self.assertIn("next", response.data)
            self.assertIn("previous", response.data)

        self.assertGreaterEqual(len(response.data["results"]), 1)

        first_result = response.data["results"][0]
        expected_basic_fields = {
            "id",
            "tag",
            "content_type",
            "content_type_name",
            "object_id",
            "content_object",
            "created_at",
            "updated_at",
            "uuid",
        }
        self.assertTrue(
            expected_basic_fields.issubset(set(first_result.keys()))
        )

    def test_retrieve_valid(self):
        url = self.get_tagged_item_detail_url(self.tagged_item.id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "tag",
            "content_type",
            "content_type_name",
            "object_id",
            "content_object",
            "created_at",
            "updated_at",
            "uuid",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_retrieve_invalid(self):
        invalid_tagged_item_id = 9999
        url = self.get_tagged_item_detail_url(invalid_tagged_item_id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_create_valid(self):
        new_product = ProductFactory()
        content_type = ContentType.objects.get_for_model(new_product)

        payload = {
            "tag_id": self.tag.id,
            "content_type": content_type.id,
            "object_id": new_product.id,
        }

        url = self.get_tagged_item_list_url()
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        expected_fields = {
            "id",
            "tag",
            "content_type",
            "content_type_name",
            "object_id",
            "content_object",
            "created_at",
            "updated_at",
            "uuid",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_create_invalid_tag(self):
        new_product = ProductFactory()
        content_type = ContentType.objects.get_for_model(new_product)

        payload = {
            "tag_id": 99999,
            "content_type": content_type.id,
            "object_id": new_product.id,
        }

        url = self.get_tagged_item_list_url()
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("tag_id", response.data)

    def test_create_inactive_tag(self):
        new_product = ProductFactory()
        content_type = ContentType.objects.get_for_model(new_product)

        payload = {
            "tag_id": self.inactive_tag.id,
            "content_type": content_type.id,
            "object_id": new_product.id,
        }

        url = self.get_tagged_item_list_url()
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("tag_id", response.data)

    def test_update_valid(self):
        new_tag = TagFactory(active=True)
        payload = {
            "tag_id": new_tag.id,
            "content_type": self.tagged_item.content_type.id,
            "object_id": self.tagged_item.object_id,
        }

        url = self.get_tagged_item_detail_url(self.tagged_item.id)
        response = self.client.put(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "tag",
            "content_type",
            "content_type_name",
            "object_id",
            "content_object",
            "created_at",
            "updated_at",
            "uuid",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_destroy_valid(self):
        tagged_item_to_delete = TaggedProductFactory(
            tag=self.tag, content_object=ProductFactory()
        )

        url = self.get_tagged_item_detail_url(tagged_item_to_delete.id)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        self.assertFalse(
            TaggedItem.objects.filter(id=tagged_item_to_delete.id).exists()
        )

    def test_destroy_invalid(self):
        invalid_tagged_item_id = 9999

        url = self.get_tagged_item_detail_url(invalid_tagged_item_id)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_filtering_by_tag(self):
        url = self.get_tagged_item_list_url()
        response = self.client.get(url, {"tag": self.tag.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for tagged_item_data in response.data["results"]:
            self.assertEqual(tagged_item_data["tag"]["id"], self.tag.id)

    def test_filtering_by_content_type(self):
        url = self.get_tagged_item_list_url()
        response = self.client.get(url, {"content_type": "product"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for tagged_item_data in response.data["results"]:
            self.assertEqual(tagged_item_data["content_type_name"], "product")

    def test_filtering_by_object_id(self):
        url = self.get_tagged_item_list_url()
        response = self.client.get(url, {"object_id": self.product.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        for tagged_item_data in response.data["results"]:
            self.assertEqual(tagged_item_data["object_id"], self.product.id)

    def test_filtering_by_tag_label(self):
        url = self.get_tagged_item_list_url()
        response = self.client.get(url, {"tag__label": "test"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)

    def test_ordering_functionality(self):
        url = self.get_tagged_item_list_url()
        response = self.client.get(url, {"ordering": "created_at"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)

    def test_search_functionality(self):
        url = self.get_tagged_item_list_url()
        response = self.client.get(url, {"search": "test"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
