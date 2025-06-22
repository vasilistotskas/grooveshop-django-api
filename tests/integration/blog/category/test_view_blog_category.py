from django.conf import settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.factories.category import BlogCategoryFactory
from blog.factories.post import BlogPostFactory
from blog.models.category import BlogCategory
from blog.serializers.category import (
    BlogCategorySerializer,
)
from core.utils.testing import TestURLFixerMixin

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class BlogCategoryViewSetTestCase(TestURLFixerMixin, APITestCase):
    def setUp(self):
        self.category = BlogCategoryFactory(slug="test-category")
        self.parent_category = BlogCategoryFactory(slug="parent-category")
        self.child_category = BlogCategoryFactory(
            slug="child-category", parent=self.parent_category
        )
        self.grandchild_category = BlogCategoryFactory(
            slug="grandchild-category", parent=self.child_category
        )

        self.post_in_parent = BlogPostFactory(category=self.parent_category)
        self.post_in_child = BlogPostFactory(category=self.child_category)

    def get_category_detail_url(self, pk):
        return reverse("blog-category-detail", args=[pk])

    def get_category_list_url(self):
        return reverse("blog-category-list")

    def get_category_posts_url(self, pk):
        return reverse("blog-category-posts", args=[pk])

    def get_category_children_url(self, pk):
        return reverse("blog-category-children", args=[pk])

    def get_category_descendants_url(self, pk):
        return reverse("blog-category-descendants", args=[pk])

    def get_category_ancestors_url(self, pk):
        return reverse("blog-category-ancestors", args=[pk])

    def get_category_siblings_url(self, pk):
        return reverse("blog-category-siblings", args=[pk])

    def get_category_tree_url(self):
        return reverse("blog-category-tree")

    def get_category_reorder_url(self):
        return reverse("blog-category-reorder")

    def test_list_uses_correct_serializer(self):
        url = self.get_category_list_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        if len(response.data["results"]) > 0:
            first_result = response.data["results"][0]
            expected_fields = {
                "id",
                "translations",
                "slug",
                "parent",
                "level",
                "sort_order",
                "post_count",
                "has_children",
                "main_image_path",
                "created_at",
                "updated_at",
            }
            self.assertTrue(expected_fields.issubset(set(first_result.keys())))

    def test_retrieve_uses_correct_serializer(self):
        url = self.get_category_detail_url(self.parent_category.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "translations",
            "slug",
            "parent",
            "level",
            "sort_order",
            "post_count",
            "has_children",
            "main_image_path",
            "created_at",
            "updated_at",
            "children",
            "ancestors",
            "siblings_count",
            "descendants_count",
            "recursive_post_count",
            "category_path",
            "tree_id",
            "uuid",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_create_request_response_serializers(self):
        payload = {
            "slug": "new-category",
            "translations": {
                default_language: {
                    "name": "New Category",
                    "description": "New category description",
                }
            },
        }

        url = self.get_category_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        expected_fields = {
            "id",
            "translations",
            "slug",
            "parent",
            "level",
            "sort_order",
            "post_count",
            "has_children",
            "main_image_path",
            "created_at",
            "updated_at",
            "children",
            "ancestors",
            "siblings_count",
            "descendants_count",
            "recursive_post_count",
            "category_path",
            "tree_id",
            "uuid",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_update_request_response_serializers(self):
        payload = {
            "slug": "updated-category",
            "translations": {
                default_language: {
                    "name": "Updated Category",
                    "description": "Updated description",
                }
            },
        }

        url = self.get_category_detail_url(self.category.id)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "translations",
            "slug",
            "parent",
            "level",
            "sort_order",
            "post_count",
            "has_children",
            "main_image_path",
            "created_at",
            "updated_at",
            "children",
            "ancestors",
            "siblings_count",
            "descendants_count",
            "recursive_post_count",
            "category_path",
            "tree_id",
            "uuid",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_partial_update_request_response_serializers(self):
        payload = {
            "translations": {
                default_language: {"name": "Partially Updated Category"}
            }
        }

        url = self.get_category_detail_url(self.category.id)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        expected_fields = {
            "id",
            "translations",
            "slug",
            "parent",
            "level",
            "sort_order",
            "post_count",
            "has_children",
            "main_image_path",
            "created_at",
            "updated_at",
            "children",
            "ancestors",
            "siblings_count",
            "descendants_count",
            "recursive_post_count",
            "category_path",
            "tree_id",
            "uuid",
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_posts_endpoint(self):
        url = self.get_category_posts_url(self.parent_category.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        self.assertTrue(len(response.data["results"]) >= 1)

    def test_posts_endpoint_recursive(self):
        url = self.get_category_posts_url(self.parent_category.id)
        response = self.client.get(url, {"recursive": "true"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        self.assertTrue(len(response.data["results"]) >= 2)

    def test_children_endpoint(self):
        url = self.get_category_children_url(self.parent_category.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        self.assertTrue(len(response.data["results"]) >= 1)
        child_ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.child_category.id, child_ids)

    def test_descendants_endpoint(self):
        url = self.get_category_descendants_url(self.parent_category.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        self.assertTrue(len(response.data["results"]) >= 2)
        descendant_ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.child_category.id, descendant_ids)
        self.assertIn(self.grandchild_category.id, descendant_ids)

    def test_ancestors_endpoint(self):
        url = self.get_category_ancestors_url(self.grandchild_category.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        self.assertTrue(len(response.data["results"]) >= 2)
        ancestor_ids = [item["id"] for item in response.data["results"]]
        self.assertIn(self.parent_category.id, ancestor_ids)
        self.assertIn(self.child_category.id, ancestor_ids)

    def test_siblings_endpoint(self):
        sibling_category = BlogCategoryFactory(
            slug="sibling-category", parent=self.parent_category
        )

        url = self.get_category_siblings_url(self.child_category.id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        if len(response.data["results"]) > 0:
            sibling_ids = [item["id"] for item in response.data["results"]]
            self.assertIn(sibling_category.id, sibling_ids)

    def test_tree_endpoint(self):
        url = self.get_category_tree_url()
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("results", response.data)

        self.assertTrue(len(response.data["results"]) >= 1)

    def test_reorder_endpoint(self):
        payload = {
            "categories": [
                {"id": self.category.id, "sort_order": 10},
                {"id": self.parent_category.id, "sort_order": 20},
            ]
        }

        url = self.get_category_reorder_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("updated_count", response.data)
        self.assertIn("message", response.data)
        self.assertEqual(response.data["updated_count"], 2)

    def test_filtering_functionality(self):
        url = self.get_category_list_url()

        response = self.client.get(url, {"parent": self.parent_category.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url, {"level": 0})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_ordering_functionality(self):
        url = self.get_category_list_url()

        response = self.client.get(url, {"ordering": "sort_order"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.get(url, {"ordering": "-created_at"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_search_functionality(self):
        url = self.get_category_list_url()

        response = self.client.get(url, {"search": "test"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_tree_parameter_in_list(self):
        url = self.get_category_list_url()
        response = self.client.get(url, {"tree": "true"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsInstance(response.data, list)

    def test_validation_errors_consistent(self):
        payload = {
            "slug": "",
            "translations": {default_language: {"name": "Test Category"}},
        }

        url = self.get_category_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("slug", response.data)

    def test_consistency_with_manual_serializer_instantiation(self):
        manual_serializer = BlogCategorySerializer(self.category)

        url = self.get_category_detail_url(self.category.id)
        response = self.client.get(url)

        manual_fields = set(manual_serializer.data.keys())
        api_fields = set(response.data.keys())

        self.assertTrue(manual_fields.issubset(api_fields))

    def test_delete_endpoint(self):
        category_to_delete = BlogCategoryFactory(slug="delete-me")

        url = self.get_category_detail_url(category_to_delete.id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            BlogCategory.objects.filter(id=category_to_delete.id).exists()
        )

    def test_list(self):
        url = self.get_category_list_url()
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("results", response.data)
        self.assertIsInstance(response.data["results"], list)

        categories_count = BlogCategory.objects.count()
        self.assertEqual(len(response.data["results"]), categories_count)

        if categories_count > 0:
            first_result = response.data["results"][0]
            self.assertIn("translations", first_result)
            self.assertIn("slug", first_result)

    def test_create_valid(self):
        payload = {
            "slug": "new-category",
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"New Category Name in {language_name}",
                "description": f"New Category Description in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_category_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_create_invalid(self):
        payload = {
            "slug": False,
            "image": "invalid_image",
            "translations": {
                "invalid_lang_code": {
                    "description": "Translation for invalid language code",
                },
            },
        }

        url = self.get_category_list_url()
        response = self.client.post(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_retrieve_valid(self):
        url = self.get_category_detail_url(self.category.pk)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertIn("slug", response.data)
        self.assertEqual(response.data["slug"], self.category.slug)

        self.assertIn("translations", response.data)

    def test_retrieve_invalid(self):
        invalid_category_id = 9999
        url = self.get_category_detail_url(invalid_category_id)
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_update_valid(self):
        payload = {
            "slug": "updated-category",
            "translations": {},
        }

        for language in languages:
            language_code = language[0]
            language_name = language[1]

            translation_payload = {
                "name": f"Updated Category Name in {language_name}",
                "description": f"Updated Category Description in {language_name}",
            }

            payload["translations"][language_code] = translation_payload

        url = self.get_category_detail_url(self.category.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_update_invalid(self):
        payload = {
            "slug": False,
            "image": "invalid_image",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                    "description": "Translation for invalid language code",
                },
            },
        }

        url = self.get_category_detail_url(self.category.pk)
        response = self.client.put(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_partial_update_valid(self):
        payload = {
            "translations": {
                default_language: {
                    "name": f"Partial update for {default_language}",
                },
            },
        }

        url = self.get_category_detail_url(self.category.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_partial_update_invalid(self):
        payload = {
            "slug": "",
            "translations": {
                "invalid_lang_code": {
                    "name": "Translation for invalid language code",
                    "description": "Translation for invalid language code",
                },
            },
        }

        url = self.get_category_detail_url(self.category.pk)
        response = self.client.patch(url, data=payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_destroy_valid(self):
        url = self.get_category_detail_url(self.category.pk)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            BlogCategory.objects.filter(pk=self.category.pk).exists()
        )

    def test_destroy_invalid(self):
        invalid_category_id = 9999
        url = self.get_category_detail_url(invalid_category_id)
        response = self.client.delete(url)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
