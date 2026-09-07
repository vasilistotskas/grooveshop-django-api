from datetime import timedelta
from unittest.mock import patch

from django.db.models import signals
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from blog.factories.author import BlogAuthorFactory
from blog.factories.category import BlogCategoryFactory
from blog.factories.comment import BlogCommentFactory
from blog.factories.post import BlogPostFactory
from blog.models.comment import BlogComment
from user.factories.account import UserAccountFactory


class BlogCommentFilterTest(APITestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def setUp(self):
        from blog.models.author import BlogAuthor
        from blog.models.category import BlogCategory
        from blog.models.comment import BlogComment
        from blog.models.post import BlogPost
        from user.models.account import UserAccount

        BlogComment.objects.all().delete()
        BlogPost.objects.all().delete()
        BlogAuthor.objects.all().delete()
        BlogCategory.objects.all().delete()
        UserAccount.objects.filter(email__contains="@example.com").delete()

        self.now = timezone.now()

        import uuid

        unique_id = str(uuid.uuid4())[:8]

        self.user1 = UserAccountFactory(
            email=f"user1-{unique_id}@example.com",
            is_staff=True,
            is_active=True,
        )
        self.user2 = UserAccountFactory(
            email=f"user2-{unique_id}@example.com",
            is_staff=False,
            is_active=True,
        )
        self.user3 = UserAccountFactory(
            email=f"inactive-{unique_id}@example.com",
            is_staff=False,
            is_active=False,
        )

        self.category = BlogCategoryFactory()
        self.category.set_current_language("en")
        self.category.name = f"Test Category {unique_id}"
        self.category.save()

        self.author = BlogAuthorFactory()
        self.author.user.first_name = "Test"
        self.author.user.last_name = f"Author {unique_id}"
        self.author.user.save()
        self.author.set_current_language("en")
        self.author.bio = f"Bio for Test Author {unique_id}"
        self.author.save()

        self.post1 = BlogPostFactory(
            category=self.category,
            author=self.author,
            is_published=True,
            slug=f"first-post-{unique_id}",
        )
        self.post1.set_current_language("en")
        self.post1.title = f"First Blog Post {unique_id}"
        self.post1.save()

        # Published. These tests exercise CONTENT and USER filters, and
        # the second post's publication state is incidental to them —
        # but this client is anonymous, and a comment on an unpublished
        # post is no longer returned to one. That visibility rule has
        # its own test in test_view_blog_comment.py rather than being
        # asserted here by accident.
        self.post2 = BlogPostFactory(
            category=self.category,
            author=self.author,
            is_published=True,
            slug=f"second-post-{unique_id}",
        )
        self.post2.set_current_language("en")
        self.post2.title = f"Second Blog Post {unique_id}"
        self.post2.save()

        self.comment1 = BlogCommentFactory(
            post=self.post1, user=self.user1, parent=None, approved=True
        )
        self.comment1.created_at = self.now - timedelta(days=10)
        self.comment1.save()
        self.comment1.set_current_language("en")
        self.comment1.content = "This is a great article about technology!"
        self.comment1.save()
        # Mute m2m_changed signal to prevent notification creation during tests
        with patch.object(signals.m2m_changed, "send"):
            for i in range(5):
                user = UserAccountFactory()
                self.comment1.likes.add(user)

        self.reply1_1 = BlogCommentFactory(
            post=self.post1,
            user=self.user2,
            parent=self.comment1,
            approved=True,
        )
        self.reply1_1.created_at = self.now - timedelta(days=8)
        self.reply1_1.save()
        self.reply1_1.set_current_language("en")
        self.reply1_1.content = "I agree completely!"
        self.reply1_1.save()
        # Mute m2m_changed signal to prevent notification creation during tests
        with patch.object(signals.m2m_changed, "send"):
            self.reply1_1.likes.add(self.user1)

        self.reply1_1_1 = BlogCommentFactory(
            post=self.post1,
            user=None,
            parent=self.reply1_1,
            approved=True,
        )
        self.reply1_1_1.created_at = self.now - timedelta(days=6)
        self.reply1_1_1.save()
        self.reply1_1_1.set_current_language("en")
        self.reply1_1_1.content = "Thanks for sharing"
        self.reply1_1_1.save()

        self.reply1_2 = BlogCommentFactory(
            post=self.post1,
            user=self.user2,
            parent=self.comment1,
            approved=False,
        )
        self.reply1_2.created_at = self.now - timedelta(days=7)
        self.reply1_2.save()
        self.reply1_2.set_current_language("en")
        self.reply1_2.content = "Spam content"
        self.reply1_2.save()

        self.comment2 = BlogCommentFactory(
            post=self.post1, user=self.user2, parent=None, approved=True
        )
        self.comment2.created_at = self.now - timedelta(days=5)
        self.comment2.save()
        self.comment2.set_current_language("en")
        self.comment2.content = "Nice"
        self.comment2.save()

        self.comment3 = BlogCommentFactory(
            post=self.post1, user=self.user3, parent=None, approved=False
        )
        self.comment3.created_at = self.now - timedelta(days=3)
        self.comment3.save()
        self.comment3.set_current_language("en")
        self.comment3.content = "This comment is not approved"
        self.comment3.save()

        self.comment4 = BlogCommentFactory(
            post=self.post2, user=None, parent=None, approved=True
        )
        self.comment4.created_at = self.now - timedelta(days=1)
        self.comment4.save()
        # This is the fixture's contentless comment, so it has to be
        # contentless in EVERY language: the factory writes one
        # translation per active language, and `hasContent=false` asks
        # whether the comment has any content at all. Blanking only the
        # English row left the Greek and German ones populated, which
        # made the same comment answer true to both sides of that
        # filter.
        self.comment4.translations.update(content="")

        BlogComment.objects.rebuild()

    def test_timestamp_filters(self):
        url = reverse("blog-comment-list")

        created_after = self.now - timedelta(days=7)
        response = self.client.get(
            url, {"created_after": created_after.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        created_before = self.now - timedelta(days=6)
        response = self.client.get(
            url, {"created_before": created_before.isoformat()}
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_uuid_filter(self):
        url = reverse("blog-comment-list")

        response = self.client.get(url, {"uuid": str(self.reply1_1.uuid)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], self.reply1_1.id)

    def test_content_filters(self):
        url = reverse("blog-comment-list")

        response = self.client.get(
            url, {"content": "technology", "post": self.post1.id}
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertGreater(
            len(results), 0, "Should find comments with 'technology'"
        )

        comment_ids = [r["id"] for r in results]
        self.assertIn(self.comment1.id, comment_ids)

        response = self.client.get(
            url,
            {
                "has_content": "true",
                "post__in": f"{self.post1.id},{self.post2.id}",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.comment1.id, result_ids)
        self.assertIn(self.comment2.id, result_ids)

        response = self.client.get(
            url,
            {
                "has_content": "false",
                "post__in": f"{self.post1.id},{self.post2.id}",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.comment4.id, result_ids)

    def test_a_translated_filter_returns_each_comment_once(self):
        """`translations__content` is one row per LANGUAGE, not per comment.

        Filtering or annotating through the join multiplied the result:
        every comment here carries an el, en and de translation, so
        `minContentLength` returned each of them three times and the
        paginated `count` agreed. `Length()` in the SELECT list also
        defeats `distinct` — the three rows differ. The predicates are
        `EXISTS` subqueries over the translation model now, see
        `core.filters.translations`.
        """
        url = reverse("blog-comment-list")

        response = self.client.get(
            url, {"minContentLength": 1, "post": self.post1.id}
        )

        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(sorted(result_ids), sorted(set(result_ids)))
        self.assertEqual(response.data["count"], len(result_ids))
        self.assertIn(self.comment1.id, result_ids)

    def test_post_relationship_filters(self):
        url = reverse("blog-comment-list")

        response = self.client.get(url, {"post": self.post1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.comment1.id, result_ids)
        self.assertIn(self.comment2.id, result_ids)

        response = self.client.get(url, {"post__slug__icontains": "first-post"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.comment1.id, result_ids)

        response = self.client.get(url, {"post__title__icontains": "First"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.comment1.id, result_ids)

        response = self.client.get(url, {"post__is_published": "true"})
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertGreater(
            len(results), 0, "Should find comments on published posts"
        )

        response = self.client.get(url, {"post__category": self.category.id})
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertGreater(len(results), 0, "Should find comments in category")

    def test_user_relationship_filters(self):
        url = reverse("blog-comment-list")

        response = self.client.get(
            url, {"user": self.user1.id, "post": self.post1.id}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.comment1.id, result_ids)

        response = self.client.get(
            url, {"user__email__icontains": "user1", "post": self.post1.id}
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]
        self.assertGreater(len(results), 0, "Should find comments by user1")

        comment_ids = [r["id"] for r in results]
        self.assertIn(self.comment1.id, comment_ids)

        response = self.client.get(
            url,
            {
                "is_anonymous": "true",
                "post__in": f"{self.post1.id},{self.post2.id}",
            },
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.reply1_1_1.id, result_ids)
        self.assertIn(self.comment4.id, result_ids)

    def test_hierarchy_filters(self):
        url = reverse("blog-comment-list")

        response = self.client.get(url, {"parent": self.comment1.id})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"parent__isnull": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"level": 0})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_like_filters(self):
        url = reverse("blog-comment-list")

        response = self.client.get(
            url, {"has_likes": "true", "post": self.post1.id}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.comment1.id, result_ids)
        self.assertIn(self.reply1_1.id, result_ids)

        response = self.client.get(url, {"min_likes": 3, "post": self.post1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.comment1.id, result_ids)

        response = self.client.get(
            url, {"liked_by": self.user1.id, "post": self.post1.id}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.reply1_1.id, result_ids)

    def test_reply_filters(self):
        url = reverse("blog-comment-list")

        response = self.client.get(url, {"has_replies": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

        response = self.client.get(url, {"is_leaf": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.data["results"], list)

    def test_tree_relationship_filters(self):
        url = reverse("blog-comment-list")

        response = self.client.get(url, {"ancestor_of": self.reply1_1_1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.comment1.id, result_ids)
        self.assertIn(self.reply1_1.id, result_ids)

        response = self.client.get(url, {"descendant_of": self.comment1.id})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.reply1_1.id, result_ids)
        self.assertIn(self.reply1_1_1.id, result_ids)

    def test_activity_filters(self):
        url = reverse("blog-comment-list")

        response = self.client.get(
            url,
            {
                "most_liked": "true",
                "post": self.post1.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]

        comment_ids = [r["id"] for r in results]
        self.assertIn(
            self.comment1.id,
            comment_ids,
            f"comment1 (ID: {self.comment1.id}) not found in results",
        )

        self.assertGreater(
            len(results), 0, "most_liked filter should return results"
        )

        response = self.client.get(
            url,
            {
                "most_replied": "true",
                "post": self.post1.id,
            },
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]

        comment_ids = [r["id"] for r in results]
        self.assertIn(
            self.comment1.id,
            comment_ids,
            f"comment1 (ID: {self.comment1.id}) not found in most_replied results",
        )

    def test_camel_case_filters(self):
        url = reverse("blog-comment-list")

        # The window has to reach back past `reply1_1` (now - 8 days),
        # the only comment that satisfies all three filters at once: it
        # is the sole liked comment with a content length over 10 that
        # is not `comment1` (liked, 40 characters, but now - 10 days).
        # A 7-day window excludes every candidate and the filter can
        # only answer with an empty list -- which this asserted against
        # `reply1_1` and still passed, because `BlogCommentFactory` had
        # `django_get_or_create = ("user", "post")` and so collapsed
        # `reply1_1`, `reply1_2` and `comment2` (all `user2` on `post1`)
        # into one row whose `created_at` the last of the three moved
        # inside the window.
        created_after = self.now - timedelta(days=9)
        response = self.client.get(
            url,
            {
                "createdAfter": created_after.isoformat(),
                "hasLikes": "true",
                "minContentLength": 10,
            },
        )
        self.assertEqual(response.status_code, 200)

        result_ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(result_ids, [self.reply1_1.id])

        response = self.client.get(url, {"parentIsnull": "true"})
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.comment1.id, result_ids)
        self.assertIn(self.comment2.id, result_ids)

    def test_complex_filter_combinations(self):
        url = reverse("blog-comment-list")

        response = self.client.get(
            url,
            {
                "userIsStaff": "true",
                "hasLikes": "true",
                "postIsPublished": "true",
                "level": 0,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(response.data["results"][0]["id"], self.comment1.id)

        response = self.client.get(
            url, {"isAnonymous": "true", "isLeaf": "true", "hasContent": "true"}
        )
        self.assertEqual(response.status_code, 200)
        result_ids = [r["id"] for r in response.data["results"]]
        self.assertIn(self.reply1_1_1.id, result_ids)

    def test_filter_with_ordering(self):
        url = reverse("blog-comment-list")

        response = self.client.get(
            url, {"post": self.post1.id, "ordering": "-created_at"}
        )
        self.assertEqual(response.status_code, 200)
        results = response.data["results"]

        self.assertGreater(len(results), 0, "Should return comments")

        comment_ids = [r["id"] for r in results]
        self.assertIn(self.comment1.id, comment_ids)
        self.assertIn(self.comment2.id, comment_ids)

    def tearDown(self):
        BlogComment.objects.all().delete()
