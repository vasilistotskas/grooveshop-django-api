from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.factories.author import BlogAuthorFactory
from blog.factories.category import BlogCategoryFactory
from blog.factories.comment import BlogCommentFactory
from blog.factories.post import BlogPostFactory
from blog.factories.tag import BlogTagFactory
from user.factories.account import UserAccountFactory

User = get_user_model()


class BlogPostFilterTestCase(APITestCase):
    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.author = BlogAuthorFactory(user=self.user)

        self.category1 = BlogCategoryFactory(slug="category-1")
        self.category2 = BlogCategoryFactory(slug="category-2")

        self.tag1 = BlogTagFactory(name="Django")
        self.tag2 = BlogTagFactory(name="Python")
        self.tag3 = BlogTagFactory(name="REST")

        self.popular_post = BlogPostFactory(
            author=self.author,
            category=self.category1,
            is_published=True,
            featured=True,
            view_count=5000,
        )
        self.popular_post.tags.set([self.tag1, self.tag2, self.tag3])

        for _i in range(5):
            user = UserAccountFactory(num_addresses=0)
            self.popular_post.likes.add(user)

        for _i in range(20):
            BlogCommentFactory(post=self.popular_post, is_approved=True)

        self.average_post = BlogPostFactory(
            author=self.author,
            category=self.category1,
            is_published=True,
            featured=False,
            view_count=500,
        )
        self.average_post.tags.set([self.tag1, self.tag2])

        for _i in range(3):
            user = UserAccountFactory(num_addresses=0)
            self.average_post.likes.add(user)

        for _i in range(5):
            BlogCommentFactory(post=self.average_post)

        self.unpopular_post = BlogPostFactory(
            author=self.author,
            category=self.category2,
            is_published=True,
            featured=False,
            view_count=100,
        )
        self.unpopular_post.tags.set([self.tag1])

        user = UserAccountFactory(num_addresses=0)
        self.unpopular_post.likes.add(user)

        BlogCommentFactory(post=self.unpopular_post)

        self.popular_post.refresh_from_db()
        self.average_post.refresh_from_db()
        self.unpopular_post.refresh_from_db()

    def get_post_list_url(self):
        return reverse("blog-post-list")

    def test_filter_by_min_likes(self):
        url = f"{self.get_post_list_url()}?min_likes=4"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.popular_post.id
        )

    def test_filter_by_min_comments(self):
        url = f"{self.get_post_list_url()}?min_comments=1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreater(len(response.data["results"]), 0)

    def test_filter_by_min_tags(self):
        url = f"{self.get_post_list_url()}?min_tags=1"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertGreater(len(response.data["results"]), 0)

    def test_filter_by_featured(self):
        url = f"{self.get_post_list_url()}?featured=true"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.popular_post.id
        )

    def test_filter_by_min_view_count(self):
        url = f"{self.get_post_list_url()}?min_view_count=800"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.popular_post.id
        )

    def test_filter_by_category(self):
        url = f"{self.get_post_list_url()}?category={self.category2.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.unpopular_post.id
        )

    def test_filter_by_author(self):
        url = f"{self.get_post_list_url()}?author={self.author.id}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 3)

    def test_filter_by_author_email(self):
        url = f"{self.get_post_list_url()}?author_email={self.user.email}"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 3)

    def test_ordering_by_likes_count(self):
        url = f"{self.get_post_list_url()}?ordering=-likesCountField"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.data["results"]
        self.assertEqual(results[0]["id"], self.popular_post.id)
        self.assertEqual(results[1]["id"], self.average_post.id)
        self.assertEqual(results[2]["id"], self.unpopular_post.id)

    def test_ordering_by_comments_count(self):
        url = f"{self.get_post_list_url()}?ordering=-commentsCountField"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.data["results"]
        self.assertEqual(results[0]["id"], self.popular_post.id)
        self.assertEqual(results[1]["id"], self.average_post.id)
        self.assertEqual(results[2]["id"], self.unpopular_post.id)

    def test_ordering_by_view_count(self):
        url = f"{self.get_post_list_url()}?ordering=-viewCount"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        results = response.data["results"]
        self.assertEqual(results[0]["id"], self.popular_post.id)
        self.assertEqual(results[1]["id"], self.average_post.id)
        self.assertEqual(results[2]["id"], self.unpopular_post.id)

    def test_combined_filters(self):
        url = f"{self.get_post_list_url()}?min_likes=2&featured=false&min_view_count=200"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["id"], self.average_post.id
        )
