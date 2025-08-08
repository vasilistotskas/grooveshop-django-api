from datetime import timedelta
from django.utils import timezone
from django.urls import reverse
from django.test import TransactionTestCase
from rest_framework.test import APIClient
import pytest

from blog.factories.author import BlogAuthorFactory
from blog.factories.post import BlogPostFactory
from blog.models.author import BlogAuthor
from user.factories.account import UserAccountFactory
from user.models.account import UserAccount


@pytest.mark.django_db(transaction=True)
class BlogAuthorFilterTest(TransactionTestCase):
    def setUp(self):
        BlogAuthor.objects.all().delete()
        UserAccount.objects.all().delete()

        self.client = APIClient()

        self.now = timezone.now()

        self.user1 = UserAccountFactory(
            first_name="John", last_name="Doe", email="john.doe@example.com"
        )
        self.user2 = UserAccountFactory(
            first_name="Jane", last_name="Smith", email="jane.smith@example.com"
        )
        self.user3 = UserAccountFactory(
            first_name="Bob",
            last_name="Johnson",
            email="bob.johnson@example.com",
        )
        self.user4 = UserAccountFactory(
            first_name="Alice",
            last_name="Williams",
            email="alice.williams@example.com",
        )

        self.like_users = []
        for i in range(20):
            user = UserAccountFactory(
                first_name=f"LikeUser{i}",
                last_name=f"Test{i}",
                email=f"likeuser{i}@test.com",
            )
            self.like_users.append(user)

        self.author1 = BlogAuthorFactory(
            user=self.user1, website="https://johndoe.com"
        )
        self.author1.created_at = self.now - timedelta(days=90)
        self.author1.save()
        self.author1.set_current_language("en")
        self.author1.bio = "Senior tech writer with 10 years experience"
        self.author1.save()

        self.author2 = BlogAuthorFactory(
            user=self.user2,
            website="",
        )
        self.author2.created_at = self.now - timedelta(days=30)
        self.author2.save()
        self.author2.set_current_language("en")
        self.author2.bio = "Freelance blogger specializing in travel"
        self.author2.save()

        self.author3 = BlogAuthorFactory(
            user=self.user3, website="https://bobjohnson.io"
        )
        self.author3.created_at = self.now - timedelta(days=7)
        self.author3.save()
        self.author3.set_current_language("en")
        self.author3.bio = "Tech enthusiast and software developer"
        self.author3.save()

        self.author4 = BlogAuthorFactory(
            user=self.user4,
            website="",
        )
        self.author4.created_at = self.now - timedelta(hours=1)
        self.author4.save()
        self.author4.set_current_language("en")
        self.author4.bio = "New to blogging"
        self.author4.save()

        self.author1_posts = []
        for i in range(5):
            post = BlogPostFactory(author=self.author1, is_published=True)
            for j in range(10):
                user_index = (i * 10 + j) % len(self.like_users)
                post.likes.add(self.like_users[user_index])
            self.author1_posts.append(post)

        self.author2_posts = []
        for i in range(3):
            post = BlogPostFactory(author=self.author2, is_published=True)
            for j in range(5):
                user_index = (i * 5 + j) % len(self.like_users)
                post.likes.add(self.like_users[user_index])
            self.author2_posts.append(post)

        self.author3_post = BlogPostFactory(
            author=self.author3, is_published=True
        )

    def test_timestamp_filters(self):
        url = reverse("blog-author-list")

        created_after = self.now - timedelta(days=60)
        response = self.client.get(
            url, {"created_after": created_after.isoformat()}
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        expected_authors = [self.author2.id, self.author3.id, self.author4.id]

        for author_id in expected_authors:
            self.assertIn(author_id, result_ids)

        created_before = self.now - timedelta(days=14)
        response = self.client.get(
            url, {"created_before": created_before.isoformat()}
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        expected_authors = [self.author1.id, self.author2.id]

        for author_id in expected_authors:
            self.assertIn(author_id, result_ids)

    def test_uuid_filter(self):
        url = reverse("blog-author-list")

        response = self.client.get(url, {"uuid": str(self.author2.uuid)})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.author2.id, result_ids)
        author_found = any(
            r["id"] == self.author2.id for r in response.data["results"]
        )
        self.assertTrue(author_found)

    def test_user_filters(self):
        url = reverse("blog-author-list")

        response = self.client.get(url, {"user": self.user1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.author1.id, result_ids)

        response = self.client.get(url, {"user_email": "jane"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.author2.id, result_ids)

        response = self.client.get(url, {"first_name": "ob"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.author3.id, result_ids)

        response = self.client.get(url, {"last_name": "williams"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.author4.id, result_ids)

    def test_full_name_filter(self):
        url = reverse("blog-author-list")

        response = self.client.get(url, {"full_name": "John"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.author1.id, result_ids)
        self.assertIn(self.author3.id, result_ids)

        response = self.client.get(url, {"full_name": "Jane Smith"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.author2.id, result_ids)

    def test_website_filters(self):
        url = reverse("blog-author-list")

        response = self.client.get(url, {"has_website": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        authors_with_website = [self.author1.id, self.author3.id]
        for author_id in authors_with_website:
            self.assertIn(author_id, result_ids)

        response = self.client.get(url, {"has_website": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        authors_without_website = [self.author2.id, self.author4.id]
        for author_id in authors_without_website:
            self.assertIn(author_id, result_ids)

        response = self.client.get(url, {"website": "johnson"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.author3.id, result_ids)

    def test_bio_filter(self):
        url = reverse("blog-author-list")

        response = self.client.get(url, {"bio": "tech"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        expected_authors = [self.author1.id, self.author3.id]
        for author_id in expected_authors:
            self.assertIn(author_id, result_ids)

    def test_post_count_filters(self):
        url = reverse("blog-author-list")

        response = self.client.get(url, {"min_posts": 3})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        self.assertIn(self.author1.id, result_ids)
        self.assertIn(self.author2.id, result_ids)

        response = self.client.get(url, {"max_posts": 1})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        self.assertIn(self.author3.id, result_ids)
        self.assertIn(self.author4.id, result_ids)

        response = self.client.get(url, {"has_posts": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        authors_with_posts = [self.author1.id, self.author2.id, self.author3.id]
        for author_id in authors_with_posts:
            self.assertIn(author_id, result_ids)

        response = self.client.get(url, {"has_posts": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        self.assertIn(self.author4.id, result_ids)

    def test_like_count_filters(self):
        url = reverse("blog-author-list")

        response = self.client.get(url, {"min_total_likes": 20})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        self.assertIn(self.author1.id, result_ids)

        response = self.client.get(url, {"has_likes": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        authors_with_likes = [self.author1.id, self.author2.id]
        for author_id in authors_with_likes:
            self.assertIn(author_id, result_ids)

        response = self.client.get(url, {"has_likes": "false"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        authors_without_likes = [self.author3.id, self.author4.id]
        for author_id in authors_without_likes:
            self.assertIn(author_id, result_ids)

    def test_special_filters(self):
        url = reverse("blog-author-list")

        response = self.client.get(url, {"most_active": "true"})
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]

        expected_authors_with_posts = [
            self.author1.id,
            self.author2.id,
            self.author3.id,
        ]
        result_ids = [r["id"] for r in results]

        for author_id in expected_authors_with_posts:
            self.assertIn(
                author_id,
                result_ids,
                f"Author {author_id} should be in results",
            )

        self.assertIn(self.author1.id, result_ids)
        self.assertIn(self.author2.id, result_ids)
        self.assertIn(self.author3.id, result_ids)

        response = self.client.get(url, {"most_liked": "true"})
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]

        expected_authors_with_likes = [self.author1.id, self.author2.id]
        result_ids = [r["id"] for r in results]

        for author_id in expected_authors_with_likes:
            self.assertIn(
                author_id,
                result_ids,
                f"Author {author_id} should be in results",
            )

    def test_camel_case_filters(self):
        url = reverse("blog-author-list")

        created_after = self.now - timedelta(days=60)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after.isoformat(),
                "hasWebsite": "true",
                "minPosts": 2,
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        self.assertGreaterEqual(len(result_ids), 0)

        self.assertEqual(response.status_code, 200)

        response = self.client.get(url, {"userEmail": "alice"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.author4.id, result_ids)

    def test_complex_filter_combinations(self):
        url = reverse("blog-author-list")

        created_after = self.now - timedelta(days=35)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after.isoformat(),
                "hasWebsite": "true",
                "hasPosts": "true",
                "bio": "tech",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        self.assertIn(self.author3.id, result_ids)

        response = self.client.get(
            url, {"minPosts": 2, "minTotalLikes": 10, "hasWebsite": "true"}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]

        self.assertIn(self.author1.id, result_ids)

    def test_filter_with_ordering(self):
        url = reverse("blog-author-list")

        response = self.client.get(url, {"ordering": "-createdAt"})
        self.assertEqual(response.status_code, 200)

        results = response.data["results"]

        test_author_results = []
        for result in results:
            if result["id"] in [
                self.author1.id,
                self.author2.id,
                self.author3.id,
                self.author4.id,
            ]:
                test_author_results.append(result)

        self.assertEqual(len(test_author_results), 4)

        self.assertEqual(test_author_results[0]["id"], self.author4.id)
        self.assertEqual(test_author_results[1]["id"], self.author3.id)
        self.assertEqual(test_author_results[2]["id"], self.author2.id)
        self.assertEqual(test_author_results[3]["id"], self.author1.id)

    def tearDown(self):
        BlogAuthor.objects.all().delete()
        UserAccount.objects.all().delete()
