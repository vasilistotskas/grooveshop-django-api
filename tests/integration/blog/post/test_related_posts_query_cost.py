"""Related posts and an author's embedded posts cost the same at any size.

Both returned a bare ``BlogPost`` queryset, so serializing each post read
its translation, author, tags and three counts one query at a time: 65
queries for eight related posts, 58 for a post whose author had six.
"""

from __future__ import annotations

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from blog.factories.author import BlogAuthorFactory
from blog.factories.category import BlogCategoryFactory
from blog.factories.post import BlogPostFactory
from user.factories.account import UserAccountFactory


class BlogPostQueryCostTestCase(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = BlogAuthorFactory(user=UserAccountFactory())
        cls.category = BlogCategoryFactory()
        cls.post = BlogPostFactory(
            author=cls.author, category=cls.category, num_tags=1
        )

    def _posts(self, count):
        BlogPostFactory.create_batch(
            count, author=self.author, category=self.category, num_tags=1
        )

    def _queries(self, url) -> int:
        self.client.get(url)  # warm the content-type and tenant lookups
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return len(queries)

    def test_related_posts_query_count_does_not_grow(self):
        url = reverse("blog-post-related_posts", args=[self.post.pk])
        self._posts(2)
        small = self._queries(url)

        self._posts(5)
        large = self._queries(url)

        self.assertEqual(large, small)

    def test_post_detail_query_count_does_not_grow_with_author_posts(self):
        url = reverse("blog-post-detail", args=[self.post.pk])
        self._posts(1)
        small = self._queries(url)

        self._posts(5)
        large = self._queries(url)

        self.assertEqual(large, small)
