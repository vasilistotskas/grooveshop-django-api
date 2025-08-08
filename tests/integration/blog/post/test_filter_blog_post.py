from django.contrib.auth import get_user_model
from django.urls import reverse
from datetime import timedelta
from django.utils import timezone
from rest_framework.test import APITestCase
from blog.factories.author import BlogAuthorFactory
from blog.factories.category import BlogCategoryFactory
from blog.factories.post import BlogPostFactory
from blog.models.post import BlogPost
from user.factories.account import UserAccountFactory

User = get_user_model()


class BlogPostFilterTest(APITestCase):
    def setUp(self):
        BlogPost.objects.all().delete()

        self.author = BlogAuthorFactory(user=UserAccountFactory())
        self.category = BlogCategoryFactory()

        self.now = timezone.now()

        self.old_post = BlogPostFactory(
            author=self.author,
            category=self.category,
            is_published=True,
            featured=True,
            view_count=1000,
        )
        self.old_post.created_at = self.now - timedelta(days=60)
        self.old_post.published_at = self.now - timedelta(days=59)
        self.old_post.save()

        self.recent_post = BlogPostFactory(
            author=self.author,
            category=self.category,
            is_published=True,
            featured=False,
            view_count=500,
        )
        self.recent_post.created_at = self.now - timedelta(hours=2)
        self.recent_post.published_at = self.now - timedelta(hours=1)
        self.recent_post.save()

        self.draft_post = BlogPostFactory(
            author=self.author,
            category=self.category,
            is_published=False,
            featured=False,
            view_count=0,
        )
        self.draft_post.created_at = self.now - timedelta(hours=3)
        self.draft_post.published_at = None
        self.draft_post.save()

        self.future_post = BlogPostFactory(
            author=self.author,
            category=self.category,
            is_published=True,
            published_at=self.now + timedelta(days=7),
            featured=False,
        )
        self.future_post.created_at = self.now - timedelta(hours=4)
        self.future_post.save()

    def test_timestamp_filters(self):
        url = reverse("blog-post-list")

        created_after_date = self.now - timedelta(days=30)
        response = self.client.get(
            url,
            {"created_after": created_after_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertNotIn(self.old_post.id, result_ids)
        self.assertIn(self.recent_post.id, result_ids)
        self.assertIn(self.draft_post.id, result_ids)
        self.assertIn(self.future_post.id, result_ids)
        self.assertEqual(len(result_ids), 3)

        created_before_date = self.now - timedelta(days=30)
        response = self.client.get(
            url,
            {"created_before": created_before_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.old_post.id, result_ids)

    def test_publishable_filters(self):
        url = reverse("blog-post-list")

        response = self.client.get(url, {"currently_published": "true"})
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.old_post.id, result_ids)
        self.assertIn(self.recent_post.id, result_ids)
        self.assertNotIn(self.draft_post.id, result_ids)
        self.assertNotIn(self.future_post.id, result_ids)

        published_after_date = self.now - timedelta(days=7)
        response = self.client.get(
            url,
            {"published_after": published_after_date.isoformat()},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.recent_post.id, result_ids)
        self.assertIn(self.future_post.id, result_ids)

    def test_uuid_filter(self):
        url = reverse("blog-post-list")

        response = self.client.get(url, {"uuid": str(self.recent_post.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], self.recent_post.id)

    def test_camel_case_filters(self):
        url = reverse("blog-post-list")

        created_after_date = self.now - timedelta(days=30)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "isPublished": "true",
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)
        self.assertIn(self.recent_post.id, result_ids)
        self.assertIn(self.future_post.id, result_ids)

        response = self.client.get(
            url,
            {
                "minViewCount": 600,
                "featured": "true",
                "currentlyPublished": "true",
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.old_post.id, result_ids)

    def test_existing_filters_still_work(self):
        url = reverse("blog-post-list")

        response = self.client.get(
            url,
            {"featured": "true", "min_view_count": 500, "is_published": "true"},
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 1)
        self.assertIn(self.old_post.id, result_ids)

    def test_complex_filter_combinations(self):
        url = reverse("blog-post-list")

        created_after_date = self.now - timedelta(days=90)
        published_before_date = self.now

        response = self.client.get(
            url,
            {
                "createdAfter": created_after_date.isoformat(),
                "publishedBefore": published_before_date.isoformat(),
                "minViewCount": 100,
                "isPublished": "true",
                "ordering": "-viewCount",
            },
        )

        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(len(result_ids), 2)

        results = response.data["results"]
        self.assertEqual(results[0]["id"], self.old_post.id)
        self.assertEqual(results[1]["id"], self.recent_post.id)

    def test_filter_with_ordering(self):
        url = reverse("blog-post-list")

        response = self.client.get(
            url, {"currentlyPublished": "true", "ordering": "-createdAt"}
        )
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]
        self.assertEqual(len(results), 2)

        post_ids = [r["id"] for r in results]
        self.assertEqual(post_ids[0], self.recent_post.id)
        self.assertEqual(post_ids[1], self.old_post.id)

    def tearDown(self):
        BlogPost.objects.all().delete()
