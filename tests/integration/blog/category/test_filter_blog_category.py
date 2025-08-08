from datetime import timedelta
from django.utils import timezone
from django.urls import reverse
from django.test import TransactionTestCase
from rest_framework.test import APIClient
import pytest

from blog.factories.category import BlogCategoryFactory
from blog.factories.post import BlogPostFactory
from blog.models.category import BlogCategory


@pytest.mark.django_db(transaction=True)
class BlogCategoryFilterTest(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def setUp(self):
        BlogCategory.objects.all().delete()

        self.client = APIClient()

        self.now = timezone.now()

        self.root1 = BlogCategoryFactory(
            parent=None, slug="root1", sort_order=1
        )
        self.root1.created_at = self.now - timedelta(days=90)
        self.root1.save()
        self.root1.set_current_language("en")
        self.root1.name = "Technology"
        self.root1.description = "All about tech and gadgets"
        self.root1.save()

        self.root2 = BlogCategoryFactory(
            parent=None,
            slug="root2",
            sort_order=2,
            image="uploads/blog/root2.jpg",
        )
        self.root2.created_at = self.now - timedelta(days=60)
        self.root2.save()
        self.root2.set_current_language("en")
        self.root2.name = "Travel"
        self.root2.description = "Travel guides and tips"
        self.root2.save()

        self.root3 = BlogCategoryFactory(
            parent=None, slug="root3", sort_order=3
        )
        self.root3.created_at = self.now - timedelta(days=30)
        self.root3.save()
        self.root3.set_current_language("en")
        self.root3.name = "Lifestyle"
        self.root3.description = "Lifestyle and wellness"
        self.root3.save()

        self.child1_1 = BlogCategoryFactory(
            parent=self.root1,
            slug="child1_1",
            sort_order=1,
            image="uploads/blog/child1_1.jpg",
        )
        self.child1_1.created_at = self.now - timedelta(days=45)
        self.child1_1.save()
        self.child1_1.set_current_language("en")
        self.child1_1.name = "Software"
        self.child1_1.description = "Software development and programming"
        self.child1_1.save()

        self.child1_2 = BlogCategoryFactory(
            parent=self.root1, slug="child1_2", sort_order=2
        )
        self.child1_2.created_at = self.now - timedelta(days=40)
        self.child1_2.save()
        self.child1_2.set_current_language("en")
        self.child1_2.name = "Hardware"
        self.child1_2.description = "Computer hardware and components"
        self.child1_2.save()

        self.child2_1 = BlogCategoryFactory(
            parent=self.root2, slug="child2_1", sort_order=1
        )
        self.child2_1.created_at = self.now - timedelta(days=20)
        self.child2_1.save()
        self.child2_1.set_current_language("en")
        self.child2_1.name = "Europe"
        self.child2_1.description = "European travel destinations"
        self.child2_1.save()

        self.grandchild1_1_1 = BlogCategoryFactory(
            parent=self.child1_1, slug="grandchild1_1_1", sort_order=1
        )
        self.grandchild1_1_1.created_at = self.now - timedelta(days=10)
        self.grandchild1_1_1.save()
        self.grandchild1_1_1.set_current_language("en")
        self.grandchild1_1_1.name = "Python"
        self.grandchild1_1_1.description = "Python programming language"
        self.grandchild1_1_1.save()

        BlogCategory.objects.rebuild()

        for i in range(2):
            BlogPostFactory(category=self.root2, is_published=True)

        for i in range(3):
            BlogPostFactory(category=self.child1_1, is_published=True)

        BlogPostFactory(category=self.child2_1, is_published=True)

        for i in range(4):
            BlogPostFactory(category=self.grandchild1_1_1, is_published=True)

    def test_timestamp_filters(self):
        url = reverse("blog-category-list")

        created_after = self.now - timedelta(days=50)
        response = self.client.get(
            url, {"created_after": created_after.isoformat()}
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.root3.id, result_ids)
        self.assertIn(self.child1_1.id, result_ids)
        self.assertIn(self.child1_2.id, result_ids)
        self.assertIn(self.child2_1.id, result_ids)
        self.assertIn(self.grandchild1_1_1.id, result_ids)

        created_before = self.now - timedelta(days=35)
        response = self.client.get(
            url, {"created_before": created_before.isoformat()}
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        expected_old_categories = [
            self.root1.id,
            self.root2.id,
            self.child1_1.id,
            self.child1_2.id,
        ]
        for cat_id in expected_old_categories:
            self.assertIn(cat_id, result_ids)

    def test_uuid_and_sort_order_filters(self):
        url = reverse("blog-category-list")

        response = self.client.get(url, {"uuid": str(self.child1_1.uuid)})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.child1_1.id, result_ids)
        category_found = any(
            r["id"] == self.child1_1.id for r in response.data["results"]
        )
        self.assertTrue(category_found)

        response = self.client.get(url, {"sort_order": 1})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_categories = [
            self.root1.id,
            self.child1_1.id,
            self.child2_1.id,
            self.grandchild1_1_1.id,
        ]
        for cat_id in expected_categories:
            self.assertIn(cat_id, result_ids)

        response = self.client.get(url, {"sort_order_min": 2})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.root2.id, result_ids)
        self.assertIn(self.root3.id, result_ids)
        self.assertIn(self.child1_2.id, result_ids)

    def test_hierarchy_filters(self):
        url = reverse("blog-category-list")

        response = self.client.get(url, {"parent": self.root1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_children = [self.child1_1.id, self.child1_2.id]
        for cat_id in expected_children:
            self.assertIn(cat_id, result_ids)

        response = self.client.get(url, {"parent__isnull": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_roots = [self.root1.id, self.root2.id, self.root3.id]
        for cat_id in expected_roots:
            self.assertIn(cat_id, result_ids)

        response = self.client.get(url, {"parent__isnull": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_non_roots = [
            self.child1_1.id,
            self.child1_2.id,
            self.child2_1.id,
            self.grandchild1_1_1.id,
        ]
        for cat_id in expected_non_roots:
            self.assertIn(cat_id, result_ids)

    def test_level_filters(self):
        url = reverse("blog-category-list")

        response = self.client.get(url, {"level": 0})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_level_0 = [self.root1.id, self.root2.id, self.root3.id]
        for cat_id in expected_level_0:
            self.assertIn(cat_id, result_ids)

        response = self.client.get(url, {"level": 1})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_level_1 = [
            self.child1_1.id,
            self.child1_2.id,
            self.child2_1.id,
        ]
        for cat_id in expected_level_1:
            self.assertIn(cat_id, result_ids)

        response = self.client.get(url, {"level__gte": 1})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.child1_1.id, result_ids)
        self.assertIn(self.grandchild1_1_1.id, result_ids)

        response = self.client.get(url, {"level__lte": 1})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.root1.id, result_ids)
        self.assertIn(self.child1_1.id, result_ids)

    def test_content_filters(self):
        url = reverse("blog-category-list")

        response = self.client.get(url, {"name": "tech"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.root1.id, result_ids)

        response = self.client.get(url, {"description": "programming"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.child1_1.id, result_ids)
        self.assertIn(self.grandchild1_1_1.id, result_ids)

        response = self.client.get(url, {"slug__icontains": "child"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_categories = [
            self.child1_1.id,
            self.child1_2.id,
            self.child2_1.id,
            self.grandchild1_1_1.id,
        ]
        for cat_id in expected_categories:
            self.assertIn(cat_id, result_ids)

    def test_image_filter(self):
        url = reverse("blog-category-list")

        response = self.client.get(url, {"has_image": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.root2.id, result_ids)
        self.assertIn(self.child1_1.id, result_ids)

        response = self.client.get(url, {"has_image": "false"})
        self.assertEqual(response.status_code, 200)

    def test_post_count_filters(self):
        url = reverse("blog-category-list")

        response = self.client.get(url, {"has_posts": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_with_posts = [
            self.root2.id,
            self.child1_1.id,
            self.child2_1.id,
            self.grandchild1_1_1.id,
        ]
        for cat_id in expected_with_posts:
            self.assertIn(cat_id, result_ids)

        response = self.client.get(url, {"has_posts": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_without_posts = [
            self.root1.id,
            self.root3.id,
            self.child1_2.id,
        ]
        for cat_id in expected_without_posts:
            self.assertIn(cat_id, result_ids)

        response = self.client.get(url, {"min_post_count": 2})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_min_2_posts = [
            self.root2.id,
            self.child1_1.id,
            self.grandchild1_1_1.id,
        ]
        for cat_id in expected_min_2_posts:
            self.assertIn(cat_id, result_ids)

        response = self.client.get(url, {"max_post_count": 2})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.root2.id, result_ids)
        self.assertIn(self.child2_1.id, result_ids)

    def test_recursive_post_filters(self):
        url = reverse("blog-category-list")

        response = self.client.get(url, {"has_recursive_posts": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.root1.id, result_ids)
        self.assertIn(self.root2.id, result_ids)
        self.assertIn(self.child1_1.id, result_ids)

        response = self.client.get(url, {"has_recursive_posts": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.root3.id, result_ids)
        self.assertIn(self.child1_2.id, result_ids)

        response = self.client.get(url, {"min_recursive_post_count": 5})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.root1.id, result_ids)

    def test_tree_structure_filters(self):
        url = reverse("blog-category-list")

        response = self.client.get(url, {"is_leaf": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_leaf_nodes = [
            self.root3.id,
            self.child1_2.id,
            self.child2_1.id,
            self.grandchild1_1_1.id,
        ]
        for cat_id in expected_leaf_nodes:
            self.assertIn(cat_id, result_ids)

        response = self.client.get(url, {"has_children": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_with_children = [
            self.root1.id,
            self.root2.id,
            self.child1_1.id,
        ]
        for cat_id in expected_with_children:
            self.assertIn(cat_id, result_ids)

        response = self.client.get(
            url, {"ancestor_of": self.grandchild1_1_1.id}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_ancestors = [self.root1.id, self.child1_1.id]
        for cat_id in expected_ancestors:
            self.assertIn(cat_id, result_ids)

        response = self.client.get(url, {"descendant_of": self.root1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        expected_descendants = [
            self.child1_1.id,
            self.child1_2.id,
            self.grandchild1_1_1.id,
        ]
        for cat_id in expected_descendants:
            self.assertIn(cat_id, result_ids)

    def test_camel_case_filters(self):
        url = reverse("blog-category-list")

        created_after = self.now - timedelta(days=50)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after.isoformat(),
                "hasImage": "true",
                "hasPosts": "true",
                "sortOrderMax": 2,
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.child1_1.id, result_ids)

        response = self.client.get(url, {"parentIsnull": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.root1.id, result_ids)
        self.assertIn(self.root2.id, result_ids)
        self.assertIn(self.root3.id, result_ids)

    def test_complex_filter_combinations(self):
        url = reverse("blog-category-list")

        response = self.client.get(
            url, {"parentIsnull": "true", "hasPosts": "true", "level": 0}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.root2.id, result_ids)

        created_after = self.now - timedelta(days=25)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after.isoformat(),
                "description": "programming",
                "hasChildren": "false",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.grandchild1_1_1.id, result_ids)

    def test_filter_with_ordering(self):
        url = reverse("blog-category-list")

        response = self.client.get(
            url, {"isLeaf": "true", "ordering": "-createdAt"}
        )
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        self.assertEqual(results[0]["id"], self.grandchild1_1_1.id)
        self.assertEqual(results[1]["id"], self.child2_1.id)
        self.assertEqual(results[2]["id"], self.root3.id)
        self.assertEqual(results[3]["id"], self.child1_2.id)

    def tearDown(self):
        BlogCategory.objects.all().delete()
