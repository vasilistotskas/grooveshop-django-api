from django.test import TestCase

from blog.factories.author import BlogAuthorFactory
from blog.factories.comment import BlogCommentFactory
from blog.factories.post import BlogPostFactory
from blog.factories.tag import BlogTagFactory
from blog.managers.post import BlogPostQuerySet
from blog.models.post import BlogPost
from user.factories.account import UserAccountFactory


class TestBlogPostManager(TestCase):
    def setUp(self):
        self.author1 = BlogAuthorFactory(user=UserAccountFactory())
        self.author2 = BlogAuthorFactory(user=UserAccountFactory())

        self.post1 = BlogPostFactory(author=self.author1)
        self.post2 = BlogPostFactory(author=self.author2)
        self.post3 = BlogPostFactory(author=self.author1)

        self.comment1 = BlogCommentFactory(
            post=self.post1, user=UserAccountFactory()
        )
        self.comment2 = BlogCommentFactory(
            post=self.post1, user=UserAccountFactory()
        )
        self.comment3 = BlogCommentFactory(
            post=self.post2, user=UserAccountFactory()
        )

        self.tag1 = BlogTagFactory(active=True)
        self.tag2 = BlogTagFactory(active=True)
        self.tag3 = BlogTagFactory(active=False)

        self.post1.tags.add(self.tag1, self.tag2, self.tag3)
        self.post2.tags.add(self.tag1)

    def test_with_likes_count_manager_method(self):
        posts = BlogPost.objects.with_likes_count()

        assert posts.count() == 3

        for post in posts:
            assert hasattr(post, "likes_count_field")
            assert isinstance(post.likes_count_field, int)

    def test_with_comments_count_manager_method(self):
        posts = BlogPost.objects.with_comments_count()

        assert posts.count() == 3

        post1_annotated = posts.get(id=self.post1.id)
        post2_annotated = posts.get(id=self.post2.id)
        post3_annotated = posts.get(id=self.post3.id)

        assert post1_annotated.comments_count_field == 2
        assert post2_annotated.comments_count_field == 1
        assert post3_annotated.comments_count_field == 0

    def test_with_tags_count_manager_method(self):
        posts = BlogPost.objects.with_tags_count()

        assert posts.count() == 3

        post1_annotated = posts.get(id=self.post1.id)
        post2_annotated = posts.get(id=self.post2.id)
        post3_annotated = posts.get(id=self.post3.id)

        assert post1_annotated.tags_count_field == 2
        assert post2_annotated.tags_count_field == 1
        assert post3_annotated.tags_count_field == 0

    def test_with_all_annotations_manager_method(self):
        posts = BlogPost.objects.with_all_annotations()

        assert posts.count() == 3

        for post in posts:
            assert hasattr(post, "likes_count_field")
            assert hasattr(post, "comments_count_field")
            assert hasattr(post, "tags_count_field")
            assert isinstance(post.likes_count_field, int)
            assert isinstance(post.comments_count_field, int)
            assert isinstance(post.tags_count_field, int)

    def test_manager_returns_correct_queryset_type(self):
        queryset = BlogPost.objects.with_likes_count()

        assert isinstance(queryset, BlogPostQuerySet)


class TestBlogPostQuerySet(TestCase):
    def setUp(self):
        self.author1 = BlogAuthorFactory(user=UserAccountFactory())
        self.author2 = BlogAuthorFactory(user=UserAccountFactory())

        self.post1 = BlogPostFactory(author=self.author1)
        self.post2 = BlogPostFactory(author=self.author2)
        self.post3 = BlogPostFactory(author=self.author1)

        self.user1 = UserAccountFactory()
        self.user2 = UserAccountFactory()
        self.user3 = UserAccountFactory()

        self.post1.likes.add(self.user1, self.user2)
        self.post2.likes.add(self.user1)

        BlogCommentFactory(post=self.post1, user=self.user1)
        BlogCommentFactory(post=self.post1, user=self.user2)
        BlogCommentFactory(post=self.post1, user=self.user3)
        BlogCommentFactory(post=self.post2, user=self.user1)

        self.active_tag1 = BlogTagFactory(active=True)
        self.active_tag2 = BlogTagFactory(active=True)
        self.inactive_tag = BlogTagFactory(active=False)

        self.post1.tags.add(
            self.active_tag1, self.active_tag2, self.inactive_tag
        )
        self.post2.tags.add(self.active_tag1)
        self.post3.tags.add(self.inactive_tag)

    def test_with_likes_count_queryset_method(self):
        queryset = BlogPost.objects.filter().with_likes_count()

        assert queryset.count() == 3

        post1_annotated = queryset.get(id=self.post1.id)
        post2_annotated = queryset.get(id=self.post2.id)
        post3_annotated = queryset.get(id=self.post3.id)

        assert post1_annotated.likes_count_field == 2
        assert post2_annotated.likes_count_field == 1
        assert post3_annotated.likes_count_field == 0

    def test_with_comments_count_queryset_method(self):
        queryset = BlogPost.objects.filter().with_comments_count()

        assert queryset.count() == 3

        post1_annotated = queryset.get(id=self.post1.id)
        post2_annotated = queryset.get(id=self.post2.id)
        post3_annotated = queryset.get(id=self.post3.id)

        assert post1_annotated.comments_count_field == 3
        assert post2_annotated.comments_count_field == 1
        assert post3_annotated.comments_count_field == 0

    def test_with_tags_count_queryset_method(self):
        queryset = BlogPost.objects.filter().with_tags_count()

        assert queryset.count() == 3

        post1_annotated = queryset.get(id=self.post1.id)
        post2_annotated = queryset.get(id=self.post2.id)
        post3_annotated = queryset.get(id=self.post3.id)

        assert post1_annotated.tags_count_field == 2
        assert post2_annotated.tags_count_field == 1
        assert post3_annotated.tags_count_field == 0

    def test_with_all_annotations_queryset_method(self):
        queryset = BlogPost.objects.filter().with_all_annotations()

        assert queryset.count() == 3

        post1_annotated = queryset.get(id=self.post1.id)

        assert post1_annotated.likes_count_field == 2
        assert post1_annotated.comments_count_field == 3
        assert post1_annotated.tags_count_field == 2

    def test_queryset_chaining(self):
        queryset = (
            BlogPost.objects.filter(author=self.author1)
            .with_likes_count()
            .with_comments_count()
            .with_tags_count()
        )

        assert queryset.count() == 2

        for post in queryset:
            assert hasattr(post, "likes_count_field")
            assert hasattr(post, "comments_count_field")
            assert hasattr(post, "tags_count_field")

    def test_queryset_with_ordering(self):
        queryset = BlogPost.objects.with_likes_count().order_by(
            "-likes_count_field"
        )

        posts = list(queryset)

        assert posts[0].id == self.post1.id
        assert posts[1].id == self.post2.id
        assert posts[2].id == self.post3.id

    def test_queryset_with_filtering(self):
        queryset = BlogPost.objects.with_comments_count().filter(
            comments_count_field__gt=0
        )

        assert queryset.count() == 2
        assert self.post1 in queryset
        assert self.post2 in queryset
        assert self.post3 not in queryset

    def test_distinct_behavior_with_annotations(self):
        self.post1.likes.add(UserAccountFactory())
        BlogCommentFactory(post=self.post1, user=UserAccountFactory())

        queryset = BlogPost.objects.with_all_annotations()

        post_ids = list(queryset.values_list("id", flat=True))
        assert len(post_ids) == len(set(post_ids))

    def test_queryset_returns_correct_type(self):
        queryset = BlogPost.objects.filter().with_likes_count()

        assert isinstance(queryset, BlogPostQuerySet)

        chained = queryset.with_comments_count()
        assert isinstance(chained, BlogPostQuerySet)


class TestBlogPostManagerEdgeCases(TestCase):
    def test_empty_queryset_behavior(self):
        assert BlogPost.objects.with_likes_count().count() == 0
        assert BlogPost.objects.with_comments_count().count() == 0
        assert BlogPost.objects.with_tags_count().count() == 0
        assert BlogPost.objects.with_all_annotations().count() == 0

    def test_posts_without_relations(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)

        annotated_posts = BlogPost.objects.with_all_annotations()
        annotated_post = annotated_posts.get(id=post.id)

        assert annotated_post.likes_count_field == 0
        assert annotated_post.comments_count_field == 0
        assert annotated_post.tags_count_field == 0

    def test_posts_with_inactive_tags_only(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)

        inactive_tag1 = BlogTagFactory(active=False)
        inactive_tag2 = BlogTagFactory(active=False)
        post.tags.add(inactive_tag1, inactive_tag2)

        annotated_posts = BlogPost.objects.with_tags_count()
        annotated_post = annotated_posts.get(id=post.id)

        assert annotated_post.tags_count_field == 0

    def test_large_dataset_performance(self):
        author = BlogAuthorFactory(user=UserAccountFactory())

        posts = []
        for i in range(20):
            post = BlogPostFactory(author=author)
            posts.append(post)

            for j in range(i % 5):
                user = UserAccountFactory()
                post.likes.add(user)
                BlogCommentFactory(post=post, user=user)

                if j < 3:
                    tag = BlogTagFactory(active=True)
                    post.tags.add(tag)

        annotated_posts = BlogPost.objects.with_all_annotations()
        assert annotated_posts.count() == 20

        for post in annotated_posts:
            assert hasattr(post, "likes_count_field")
            assert hasattr(post, "comments_count_field")
            assert hasattr(post, "tags_count_field")

    def test_annotation_with_deleted_relations(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)

        user = UserAccountFactory()
        post.likes.add(user)
        comment = BlogCommentFactory(post=post, user=user)
        tag = BlogTagFactory(active=True)
        post.tags.add(tag)

        annotated_post = BlogPost.objects.with_all_annotations().get(id=post.id)
        assert annotated_post.likes_count_field == 1
        assert annotated_post.comments_count_field == 1
        assert annotated_post.tags_count_field == 1

        comment.delete()
        tag.delete()
        post.likes.remove(user)

        annotated_post = BlogPost.objects.with_all_annotations().get(id=post.id)
        assert annotated_post.likes_count_field == 0
        assert annotated_post.comments_count_field == 0
        assert annotated_post.tags_count_field == 0

    def test_manager_get_queryset_method(self):
        manager = BlogPost.objects
        queryset = manager.get_queryset()

        assert isinstance(queryset, BlogPostQuerySet)

        assert hasattr(queryset, "with_likes_count")
        assert hasattr(queryset, "with_comments_count")
        assert hasattr(queryset, "with_tags_count")
        assert hasattr(queryset, "with_all_annotations")
