import uuid

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
        self.test_id = uuid.uuid4().hex[:8]
        self.category = ProductCategoryFactory()
        self.sub_category = ProductCategoryFactory(parent=self.category)
        self.user = User.objects.create_user(
            email=f"test-{self.test_id}@example.com",
            username=f"testuser-{self.test_id}",
            password="testpass123",
        )
        self.admin_user = User.objects.create_superuser(
            email=f"admin-{self.test_id}@example.com",
            username=f"adminuser-{self.test_id}",
            password="testpass123",
        )
        self.client.force_authenticate(user=self.admin_user)

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
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

    def test_create_request_response_serializers(self):
        payload = {
            "slug": f"new-category-enhanced-{self.test_id}",
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
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        category = ProductCategory.objects.get(id=response.data["id"])
        self.assertEqual(category.slug, f"new-category-enhanced-{self.test_id}")
        self.assertEqual(category.parent.id, self.category.id)

    def test_update_request_response_serializers(self):
        payload = {
            "slug": f"updated-category-enhanced-{self.test_id}",
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
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        category = ProductCategory.objects.get(id=response.data["id"])
        self.assertEqual(
            category.slug, f"updated-category-enhanced-{self.test_id}"
        )

    def test_partial_update_request_response_serializers(self):
        payload = {
            "slug": f"partial-updated-category-{self.test_id}",
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
        }
        self.assertTrue(expected_fields.issubset(set(response.data.keys())))

        category = ProductCategory.objects.get(id=response.data["id"])
        self.assertEqual(
            category.slug, f"partial-updated-category-{self.test_id}"
        )

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
        # Refresh to ensure we have the latest translations from factory
        self.category.refresh_from_db()
        url = self.get_product_category_list_url()
        search_value = (
            self.category.safe_translation_getter("name", any_language=True)
            or ""
        )
        response = self.client.get(
            url,
            {"search": search_value},
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
            "slug": f"invalid-parent-category-{self.test_id}",
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
        unique_slug = f"complex-category-{self.test_id}"
        payload = {
            "slug": unique_slug,
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


class ProductCategoryVisibilityTestCase(APITestCase):
    """A category switched off in the admin must leave the storefront.

    The read actions are ``AllowAny`` and, until 2026-09-19, unfiltered:
    the demo store carried a pill for ``demo-chargers-cables`` on
    ``/products`` — inactive, zero products — linking to a page that
    listed nothing, and the same row reached the filter sidebar, the
    sitemap and the agent gateway's catalogue feed. Four consumers, one
    queryset.
    """

    def setUp(self):
        self.test_id = uuid.uuid4().hex[:8]
        self.visible = ProductCategoryFactory(active=True)
        self.hidden = ProductCategoryFactory(active=False)
        # Active itself, but under a parent that is not: the whole
        # branch goes, or the menu (which indexes by parent id) drops it
        # while the products page and the sitemap keep it.
        self.orphaned_child = ProductCategoryFactory(
            parent=self.hidden, active=True
        )
        self.staff = User.objects.create_superuser(
            email=f"staff-{self.test_id}@example.com",
            username=f"staffuser-{self.test_id}",
            password="testpass123",
        )

    @staticmethod
    def _ids(payload):
        """Ids out of either shape: `list` returns the paginated
        envelope, `all` returns a bare list."""
        rows = (
            payload.get("results", payload)
            if hasattr(payload, "get")
            else payload
        )
        return {row["id"] for row in rows}

    def test_anonymous_list_omits_an_inactive_category(self):
        response = self.client.get(reverse("product-category-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self._ids(response.data)
        self.assertIn(self.visible.id, ids)
        self.assertNotIn(self.hidden.id, ids)

    def test_anonymous_list_omits_a_branch_under_an_inactive_parent(self):
        response = self.client.get(reverse("product-category-list"))

        self.assertNotIn(self.orphaned_child.id, self._ids(response.data))

    def test_anonymous_all_omits_the_same_rows(self):
        # `all` is the unpaginated endpoint the header menu and the
        # filter sidebar read; it declares `filter_backends=[]`, so the
        # queryset is the only thing standing in the way.
        response = self.client.get(reverse("product-category-all"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = self._ids(response.data)
        self.assertIn(self.visible.id, ids)
        self.assertNotIn(self.hidden.id, ids)
        self.assertNotIn(self.orphaned_child.id, ids)

    def test_anonymous_detail_of_an_inactive_category_is_404(self):
        response = self.client.get(
            reverse("product-category-detail", args=[self.hidden.id])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_staff_still_see_the_whole_tree(self):
        self.client.force_authenticate(user=self.staff)
        response = self.client.get(reverse("product-category-all"))

        ids = self._ids(response.data)
        self.assertIn(self.hidden.id, ids)
        self.assertIn(self.orphaned_child.id, ids)
