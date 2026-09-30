"""``BlogAuthorFilter`` (blog/filters/author.py) through the author list.

Every case asserts the exact set of authors returned from one dataset,
so a filter the view does not apply — or a parameter django-filter
does not know — fails instead of passing on the unfiltered list.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from blog.factories.post import BlogPostFactory
from blog.models.author import BlogAuthor
from user.factories.account import UserAccountFactory

URL = reverse("blog-author-list")


class BlogAuthorFilterTest(TestCase):
    client_class = APIClient

    @classmethod
    def _author(cls, first, last, email, *, website, bio, days_old):
        user = UserAccountFactory(
            first_name=first, last_name=last, email=email, num_addresses=0
        )
        # Created directly: the factory writes a random bio in every
        # language, which a substring filter could match by chance.
        author = BlogAuthor.objects.create(user=user, website=website)
        author.set_current_language("en")
        author.bio = bio
        author.save()
        BlogAuthor.objects.filter(pk=author.pk).update(
            created_at=cls.now - days_old
        )
        return author

    @classmethod
    def setUpTestData(cls):
        cls.now = timezone.now()
        cls.john = cls._author(
            "John",
            "Doe",
            "john.doe@example.com",
            website="https://johndoe.com",
            bio="Senior tech writer",
            days_old=timedelta(days=90),
        )
        cls.jane = cls._author(
            "Jane",
            "Smith",
            "jane.smith@example.com",
            website="",
            bio="Freelance travel blogger",
            days_old=timedelta(days=30),
        )
        cls.bob = cls._author(
            "Bob",
            "Johnson",
            "bob.johnson@example.com",
            website="https://bobjohnson.io",
            bio="Tech enthusiast",
            days_old=timedelta(days=7),
        )
        cls.alice = cls._author(
            "Alice",
            "Williams",
            "alice.williams@example.com",
            website="",
            bio="New to blogging",
            days_old=timedelta(hours=1),
        )

        readers = UserAccountFactory.create_batch(3, num_addresses=0)
        # john: 3 posts, 3 likes each. jane: 2 posts, 1 like each.
        # bob: 1 unliked post. alice: nothing.
        for author, posts, likers in (
            (cls.john, 3, readers),
            (cls.jane, 2, readers[:1]),
            (cls.bob, 1, []),
        ):
            for _ in range(posts):
                post = BlogPostFactory(author=author, image=None)
                post.likes.add(*likers)

    def _authors(self, params) -> set:
        response = self.client.get(URL, params)
        self.assertEqual(response.status_code, 200, response.data)
        return {row["id"] for row in response.data["results"]}

    def test_each_filter_selects_exactly_its_authors(self):
        john, jane, bob, alice = (
            self.john.id,
            self.jane.id,
            self.bob.id,
            self.alice.id,
        )
        cases = [
            (
                {"created_after": self.now - timedelta(days=60)},
                {jane, bob, alice},
            ),
            ({"created_before": self.now - timedelta(days=14)}, {john, jane}),
            ({"uuid": str(self.jane.uuid)}, {jane}),
            ({"user": self.john.user_id}, {john}),
            ({"user_email": "jane"}, {jane}),
            ({"first_name": "ob"}, {bob}),
            ({"last_name": "williams"}, {alice}),
            ({"full_name": "John"}, {john, bob}),
            ({"full_name": "Jane Smith"}, {jane}),
            ({"has_website": "true"}, {john, bob}),
            ({"has_website": "false"}, {jane, alice}),
            ({"website": "johnson"}, {bob}),
            ({"bio": "tech"}, {john, bob}),
            ({"min_posts": 2}, {john, jane}),
            ({"max_posts": 1}, {bob, alice}),
            ({"has_posts": "true"}, {john, jane, bob}),
            ({"has_posts": "false"}, {alice}),
            ({"min_total_likes": 9}, {john}),
            ({"has_likes": "true"}, {john, jane}),
            ({"has_likes": "false"}, {bob, alice}),
            # The camelCase spelling the storefront sends.
            ({"hasWebsite": "true", "minPosts": 1}, {john, bob}),
            ({"userEmail": "alice"}, {alice}),
            (
                {
                    "createdAfter": self.now - timedelta(days=35),
                    "hasWebsite": "true",
                    "bio": "tech",
                },
                {bob},
            ),
        ]
        for params, expected in cases:
            with self.subTest(params=params):
                self.assertEqual(self._authors(params), expected)

    def test_ordering_by_newest_first(self):
        response = self.client.get(URL, {"ordering": "-createdAt"})

        self.assertEqual(
            [row["id"] for row in response.data["results"]],
            [self.alice.id, self.bob.id, self.jane.id, self.john.id],
        )

    def test_ranking_by_published_posts(self):
        response = self.client.get(URL, {"ordering": "-numberOfPosts"})

        self.assertEqual(
            [row["id"] for row in response.data["results"]],
            [self.john.id, self.jane.id, self.bob.id, self.alice.id],
        )

    def test_ranking_by_likes_counts_every_like_not_every_liker(self):
        """John's 3 readers liked each of his 3 posts: 9 likes, 3 likers."""
        response = self.client.get(URL, {"ordering": "-totalLikesReceived"})

        rows = response.data["results"]
        self.assertEqual(
            [row["id"] for row in rows[:2]], [self.john.id, self.jane.id]
        )
        self.assertEqual(rows[0]["total_likes_received"], 9)
