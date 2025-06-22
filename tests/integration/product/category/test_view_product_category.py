from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from product.factories.category import ProductCategoryFactory
from product.models.category import ProductCategory
from product.serializers.category import (
    ProductCategoryDetailSerializer,
)

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class ProductCategoryViewSetTestCase(APITestCase):
    def setUp(self):
        self.category = ProductCategoryFactory()
        self.sub_category = ProductCategoryFactory(parent=self.category)
        self.user = User.objects.create_user(
            email="test@example.com",
            username="testuser",
            password="testpass123",
        )

    def get_product_category_detail_url(self, pk):
        return reverse("product-category-detail", args=[pk])

    def get_product_category_list_url(self):
        return reverse("product-category-list")

    def test_list_uses_correct_serializer(self):
        url = self.get_product_category_list_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        if response.data["results"]:
            category_data = response.data["results"][0]
            expected_fields = {
                "id",
                "slug",
                "translations",
                "parent",
                "active",
                "level",
                "tree_id",
                "created_at",
                "updated_at",
                "uuid",
                "recursive_product_count",
            }
            self.assertTrue(expected_fields.issubset(set(category_data.keys())))

    def test_retrieve_uses_correct_serializer(self):
        url = self.get_product_category_detail_url(self.category.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "slug",
            "translations",
            "parent",
            "active",
            "level",
            "tree_id",
            "created_at",
            "updated_at",
            "uuid",
            "recursive_product_count",
            "children",
            "seo_title",
            "seo_description",
            "seo_keywords",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_create_request_response_serializers(self):
        payload = {
            "slug": "new-category-enhanced",
            "parent": self.category.id,
            "translations": {
                default_language: {
                    "name": "New Enhanced Category",
                    "description": "New Enhanced Category Description",
                },
            },
        }

        url = self.get_product_category_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        expected_fields = {
            "id",
            "slug",
            "translations",
            "parent",
            "active",
            "level",
            "tree_id",
            "created_at",
            "updated_at",
            "uuid",
            "recursive_product_count",
            "children",
            "seo_title",
            "seo_description",
            "seo_keywords",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        category = ProductCategory.objects.get(id=response.data["id"])
        self.assertEqual(category.slug, "new-category-enhanced")
        self.assertEqual(category.parent.id, self.category.id)

    def test_update_request_response_serializers(self):
        payload = {
            "slug": "updated-category-enhanced",
            "translations": {
                default_language: {
                    "name": "Updated Enhanced Category",
                    "description": "Updated Enhanced Category Description",
                },
            },
        }

        url = self.get_product_category_detail_url(self.category.id)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "slug",
            "translations",
            "parent",
            "active",
            "level",
            "tree_id",
            "created_at",
            "updated_at",
            "uuid",
            "recursive_product_count",
            "children",
            "seo_title",
            "seo_description",
            "seo_keywords",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        category = ProductCategory.objects.get(id=response.data["id"])
        self.assertEqual(category.slug, "updated-category-enhanced")

    def test_partial_update_request_response_serializers(self):
        payload = {
            "slug": "partial-updated-category-enhanced",
        }

        url = self.get_product_category_detail_url(self.category.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "slug",
            "translations",
            "parent",
            "active",
            "level",
            "tree_id",
            "created_at",
            "updated_at",
            "uuid",
            "recursive_product_count",
            "children",
            "seo_title",
            "seo_description",
            "seo_keywords",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        category = ProductCategory.objects.get(id=response.data["id"])
        self.assertEqual(category.slug, "partial-updated-category-enhanced")

    def test_filtering_functionality(self):
        parent_category = ProductCategoryFactory()
        child_category = ProductCategoryFactory(parent=parent_category)

        url = self.get_product_category_list_url()
        response = self.client.get(url, {"parent": parent_category.id})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], child_category.id)

        response = self.client.get(url, {"slug": self.category.slug})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], self.category.id)

    def test_search_functionality(self):
        url = self.get_product_category_list_url()

        response = self.client.get(
            url,
            {
                "search": self.category.safe_translation_getter(
                    "name", any_language=True
                )
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        category_ids = [cat["id"] for cat in response.data["results"]]
        self.assertIn(self.category.id, category_ids)

    def test_ordering_functionality(self):
        url = self.get_product_category_list_url()

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url, {"ordering": "id"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        if len(response.data["results"]) > 1:
            ids = [cat["id"] for cat in response.data["results"]]
            self.assertEqual(ids, sorted(ids))

    def test_validation_errors_consistent(self):
        payload = {
            "slug": "invalid-parent-category",
            "parent": 99999,
            "translations": {
                default_language: {
                    "name": "Invalid Parent Category",
                    "description": "Category with invalid parent",
                },
            },
        }

        url = self.get_product_category_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("parent", response.data)

    def test_consistency_with_manual_serializer_instantiation(self):
        url = self.get_product_category_detail_url(self.category.id)
        viewset_response = self.client.get(url)

        manual_serializer = ProductCategoryDetailSerializer(self.category)

        self.assertEqual(viewset_response.status_code, status.HTTP_200_OK)

        key_fields = ["id", "slug", "parent"]
        for field in key_fields:
            self.assertEqual(
                viewset_response.data[field], manual_serializer.data[field]
            )

    def test_create_with_complex_payload(self):
        payload = {
            "slug": "complex-category",
            "parent": self.category.id,
            "translations": {
                "en": {
                    "name": "Complex Category EN",
                    "description": "Complex Category Description EN",
                },
                "de": {
                    "name": "Complex Category DE",
                    "description": "Complex Category Description DE",
                },
            },
        }

        url = self.get_product_category_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        category = ProductCategory.objects.get(id=response.data["id"])

        category.set_current_language("en")
        self.assertEqual(category.name, "Complex Category EN")

        category.set_current_language("de")
        self.assertEqual(category.name, "Complex Category DE")

    def test_hierarchy_data_in_responses(self):
        url = self.get_product_category_detail_url(self.category.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("children", response.data)
        self.assertIn("recursive_product_count", response.data)

        if response.data["children"]:
            child_data = response.data["children"][0]
            self.assertIn("id", child_data)
            self.assertIn("slug", child_data)
            self.assertIn("translations", child_data)
