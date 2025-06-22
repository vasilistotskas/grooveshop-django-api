from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from blog.factories.author import BlogAuthorFactory
from blog.factories.post import BlogPostFactory
from blog.managers.author import BlogAuthorQuerySet
from blog.models.author import BlogAuthor
from user.factories.account import UserAccountFactory


class TestBlogAuthorManager(TestCase):
    def setUp(self):
        self.user1 = UserAccountFactory(first_name="John", last_name="Doe")
        self.user2 = UserAccountFactory(first_name="Jane", last_name="Smith")
        self.user3 = UserAccountFactory(first_name="Bob", last_name="Wilson")

        self.author_with_posts = BlogAuthorFactory(user=self.user1)
        self.author_without_posts = BlogAuthorFactory(user=self.user2)
        self.author_with_old_posts = BlogAuthorFactory(user=self.user3)

        self.recent_post = BlogPostFactory(author=self.author_with_posts)

        old_date = timezone.now() - timedelta(days=200)
        self.old_post = BlogPostFactory(author=self.author_with_old_posts)
        self.old_post.created_at = old_date
        self.old_post.save()

    def test_with_posts_manager_method(self):
        authors_with_posts = BlogAuthor.objects.with_posts()

        assert self.author_with_posts in authors_with_posts
        assert self.author_with_old_posts in authors_with_posts

        assert self.author_without_posts not in authors_with_posts

        assert authors_with_posts.count() == 2

    def test_without_posts_manager_method(self):
        authors_without_posts = BlogAuthor.objects.without_posts()

        assert self.author_without_posts in authors_without_posts

        assert self.author_with_posts not in authors_without_posts
        assert self.author_with_old_posts not in authors_without_posts

        assert authors_without_posts.count() == 1

    def test_active_manager_method(self):
        active_authors = BlogAuthor.objects.active()

        assert self.author_with_posts in active_authors

        assert self.author_with_old_posts not in active_authors

        assert self.author_without_posts not in active_authors

        assert active_authors.count() == 1

    def test_with_website_manager_method(self):
        author_no_website = BlogAuthorFactory(
            user=UserAccountFactory(), website=""
        )

        authors_with_website = BlogAuthor.objects.with_website()

        assert authors_with_website.count() >= 3

        assert author_no_website not in authors_with_website

    def test_with_bio_manager_method(self):
        author_no_bio = BlogAuthorFactory(user=UserAccountFactory())
        for translation in author_no_bio.translations.all():
            translation.bio = ""
            translation.save()

        authors_with_bio = BlogAuthor.objects.with_bio()

        assert self.author_with_posts in authors_with_bio
        assert self.author_without_posts in authors_with_bio
        assert self.author_with_old_posts in authors_with_bio

        assert author_no_bio not in authors_with_bio

    def test_with_user_details_manager_method(self):
        authors = BlogAuthor.objects.with_user_details()

        assert authors.count() == 3

        assert self.author_with_posts in authors
        assert self.author_without_posts in authors
        assert self.author_with_old_posts in authors

        queryset = BlogAuthor.objects.with_user_details()
        query_str = str(queryset.query)

        assert 'INNER JOIN "user_useraccount"' in query_str

    def test_manager_returns_correct_queryset_type(self):
        queryset = BlogAuthor.objects.with_posts()

        assert isinstance(queryset, BlogAuthorQuerySet)


class TestBlogAuthorQuerySet(TestCase):
    def setUp(self):
        self.user1 = UserAccountFactory(first_name="Alice", last_name="Johnson")
        self.user2 = UserAccountFactory(first_name="Charlie", last_name="Brown")
        self.user3 = UserAccountFactory(first_name="Diana", last_name="Prince")

        self.author1 = BlogAuthorFactory(user=self.user1)
        self.author2 = BlogAuthorFactory(user=self.user2)
        self.author3 = BlogAuthorFactory(user=self.user3)

        self.recent_post1 = BlogPostFactory(author=self.author1)
        self.recent_post2 = BlogPostFactory(author=self.author2)

        old_date = timezone.now() - timedelta(days=200)
        self.old_post = BlogPostFactory(author=self.author3)
        self.old_post.created_at = old_date
        self.old_post.save()

    def test_with_posts_queryset_method(self):
        queryset = BlogAuthor.objects.filter().with_posts()

        assert self.author1 in queryset
        assert self.author2 in queryset
        assert self.author3 in queryset

        assert queryset.count() == 3

    def test_without_posts_queryset_method(self):
        author_no_posts = BlogAuthorFactory(user=UserAccountFactory())

        queryset = BlogAuthor.objects.filter().without_posts()

        assert author_no_posts in queryset
        assert self.author1 not in queryset
        assert self.author2 not in queryset
        assert self.author3 not in queryset

        assert queryset.count() == 1

    def test_active_queryset_method(self):
        queryset = BlogAuthor.objects.filter().active()

        assert self.author1 in queryset
        assert self.author2 in queryset

        assert self.author3 not in queryset

        assert queryset.count() == 2

    def test_with_website_queryset_method(self):
        author_no_website = BlogAuthorFactory(
            user=UserAccountFactory(), website=""
        )

        queryset = BlogAuthor.objects.filter().with_website()

        assert self.author1 in queryset
        assert self.author2 in queryset
        assert self.author3 in queryset

        assert author_no_website not in queryset

    def test_with_bio_queryset_method(self):
        author_no_bio = BlogAuthorFactory(user=UserAccountFactory())
        for translation in author_no_bio.translations.all():
            translation.bio = ""
            translation.save()

        queryset = BlogAuthor.objects.filter().with_bio()

        assert self.author1 in queryset
        assert self.author2 in queryset
        assert self.author3 in queryset

        assert author_no_bio not in queryset

    def test_with_user_details_queryset_method(self):
        queryset = BlogAuthor.objects.filter().with_user_details()

        assert queryset.count() == 3

        assert self.author1 in queryset
        assert self.author2 in queryset
        assert self.author3 in queryset

        query_str = str(queryset.query)
        assert 'INNER JOIN "user_useraccount"' in query_str

    def test_queryset_chaining(self):
        queryset = (
            BlogAuthor.objects.filter()
            .with_posts()
            .active()
            .with_website()
            .with_bio()
        )

        assert self.author1 in queryset
        assert self.author2 in queryset
        assert self.author3 not in queryset

        assert queryset.count() == 2

    def test_queryset_returns_correct_type(self):
        queryset = BlogAuthor.objects.filter().with_posts()

        assert isinstance(queryset, BlogAuthorQuerySet)

        chained = queryset.active()
        assert isinstance(chained, BlogAuthorQuerySet)


class TestBlogAuthorManagerEdgeCases(TestCase):
    def test_empty_queryset_behavior(self):
        assert BlogAuthor.objects.with_posts().count() == 0
        assert BlogAuthor.objects.without_posts().count() == 0
        assert BlogAuthor.objects.active().count() == 0
        assert BlogAuthor.objects.with_website().count() == 0
        assert BlogAuthor.objects.with_bio().count() == 0

    def test_active_cutoff_date_boundary(self):
        author = BlogAuthorFactory(user=UserAccountFactory())

        boundary_date = timezone.now() - timedelta(days=179)
        post = BlogPostFactory(author=author)
        post.created_at = boundary_date
        post.save()

        active_authors = BlogAuthor.objects.active()

        assert author in active_authors

        old_author = BlogAuthorFactory(user=UserAccountFactory())
        old_date = timezone.now() - timedelta(days=181)
        old_post = BlogPostFactory(author=old_author)
        old_post.created_at = old_date
        old_post.save()

        active_authors = BlogAuthor.objects.active()

        assert old_author not in active_authors

    def test_distinct_behavior(self):
        author = BlogAuthorFactory(user=UserAccountFactory())

        BlogPostFactory(author=author)
        BlogPostFactory(author=author)
        BlogPostFactory(author=author)

        authors_with_posts = BlogAuthor.objects.with_posts()
        active_authors = BlogAuthor.objects.active()

        assert authors_with_posts.count() == 1
        assert active_authors.count() == 1
        assert author in authors_with_posts
        assert author in active_authors

    def test_performance_with_large_dataset(self):
        authors = []
        for i in range(10):
            user = UserAccountFactory(first_name=f"User{i}")
            author = BlogAuthorFactory(
                user=user, website=f"https://user{i}.com"
            )
            authors.append(author)

            if i % 2 == 0:
                BlogPostFactory(author=author)

        with_posts = BlogAuthor.objects.with_posts()
        without_posts = BlogAuthor.objects.without_posts()
        active = BlogAuthor.objects.active()
        with_website = BlogAuthor.objects.with_website()
        with_bio = BlogAuthor.objects.with_bio()

        assert with_posts.count() == 5
        assert without_posts.count() == 5
        assert active.count() == 5
        assert with_website.count() == 10
        assert with_bio.count() == 10

    def test_manager_get_queryset_method(self):
        manager = BlogAuthor.objects
        queryset = manager.get_queryset()

        assert isinstance(queryset, BlogAuthorQuerySet)

        assert hasattr(queryset, "with_posts")
        assert hasattr(queryset, "without_posts")
        assert hasattr(queryset, "active")
        assert hasattr(queryset, "with_website")
        assert hasattr(queryset, "with_bio")
        assert hasattr(queryset, "with_user_details")
