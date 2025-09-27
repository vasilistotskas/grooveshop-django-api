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
            post=self.post1, user=UserAccountFactory(), approved=True
        )
        self.comment2 = BlogCommentFactory(
            post=self.post1, user=UserAccountFactory(), approved=True
        )
        self.comment3 = BlogCommentFactory(
            post=self.post1,
            user=UserAccountFactory(),
            approved=False,
        )
        self.comment4 = BlogCommentFactory(
            post=self.post2, user=UserAccountFactory(), approved=True
        )

        self.tag1 = BlogTagFactory(active=True)
        self.tag2 = BlogTagFactory(active=True)
        self.tag3 = BlogTagFactory(active=False)

        self.post1.tags.add(self.tag1, self.tag2, self.tag3)
        self.post2.tags.add(self.tag1)

    def test_with_likes_count_manager_method(self):
        posts = BlogPost.objects.with_likes_count_annotation()

        assert posts.count() == 3

        for post in posts:
            assert hasattr(post, "likes_count_annotation")
            assert isinstance(post.likes_count_annotation, int)

    def test_with_comments_count_manager_method(self):
        posts = BlogPost.objects.with_comments_count_annotation()

        assert posts.count() == 3

        post1_annotated = posts.get(id=self.post1.id)
        post2_annotated = posts.get(id=self.post2.id)
        post3_annotated = posts.get(id=self.post3.id)

        assert post1_annotated.comments_count_annotation == 2
        assert post2_annotated.comments_count_annotation == 1
        assert post3_annotated.comments_count_annotation == 0

    def test_with_tags_count_manager_method(self):
        posts = BlogPost.objects.with_tags_count_annotation()

        assert posts.count() == 3

        post1_annotated = posts.get(id=self.post1.id)
        post2_annotated = posts.get(id=self.post2.id)
        post3_annotated = posts.get(id=self.post3.id)

        assert post1_annotated.tags_count_annotation == 2
        assert post2_annotated.tags_count_annotation == 1
        assert post3_annotated.tags_count_annotation == 0

    def test_manager_returns_correct_queryset_type(self):
        queryset = BlogPost.objects.with_likes_count_annotation()

        assert isinstance(queryset, BlogPostQuerySet)

    def test_model_properties_vs_annotations(self):
        posts = BlogPost.objects.with_comments_count_annotation()

        for post in posts:
            assert post.comments_count == post.comments_count_annotation


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

        BlogCommentFactory(post=self.post1, user=self.user1, approved=True)
        BlogCommentFactory(post=self.post1, user=self.user2, approved=True)
        BlogCommentFactory(post=self.post1, user=self.user3, approved=False)
        BlogCommentFactory(post=self.post2, user=self.user1, approved=True)

        self.active_tag1 = BlogTagFactory(active=True)
        self.active_tag2 = BlogTagFactory(active=True)
        self.inactive_tag = BlogTagFactory(active=False)

        self.post1.tags.add(
            self.active_tag1, self.active_tag2, self.inactive_tag
        )
        self.post2.tags.add(self.active_tag1)
        self.post3.tags.add(self.inactive_tag)

    def test_with_likes_count_queryset_method(self):
        queryset = BlogPost.objects.filter().with_likes_count_annotation()

        assert queryset.count() == 3

        post1_annotated = queryset.get(id=self.post1.id)
        post2_annotated = queryset.get(id=self.post2.id)
        post3_annotated = queryset.get(id=self.post3.id)

        assert post1_annotated.likes_count_annotation == 2
        assert post2_annotated.likes_count_annotation == 1
        assert post3_annotated.likes_count_annotation == 0

    def test_with_comments_count_queryset_method(self):
        queryset = BlogPost.objects.filter().with_comments_count_annotation()

        assert queryset.count() == 3

        post1_annotated = queryset.get(id=self.post1.id)
        post2_annotated = queryset.get(id=self.post2.id)
        post3_annotated = queryset.get(id=self.post3.id)

        assert post1_annotated.comments_count_annotation == 2
        assert post2_annotated.comments_count_annotation == 1
        assert post3_annotated.comments_count_annotation == 0

    def test_with_tags_count_queryset_method(self):
        queryset = BlogPost.objects.filter().with_tags_count_annotation()

        assert queryset.count() == 3

        post1_annotated = queryset.get(id=self.post1.id)
        post2_annotated = queryset.get(id=self.post2.id)
        post3_annotated = queryset.get(id=self.post3.id)

        assert post1_annotated.tags_count_annotation == 2
        assert post2_annotated.tags_count_annotation == 1
        assert post3_annotated.tags_count_annotation == 0

    def test_queryset_chaining(self):
        queryset = (
            BlogPost.objects.filter(author=self.author1)
            .with_likes_count_annotation()
            .with_comments_count_annotation()
            .with_tags_count_annotation()
        )

        assert queryset.count() == 2

        for post in queryset:
            assert hasattr(post, "likes_count_annotation")
            assert hasattr(post, "comments_count_annotation")
            assert hasattr(post, "tags_count_annotation")

    def test_queryset_with_ordering(self):
        queryset = BlogPost.objects.with_likes_count_annotation().order_by(
            "-likes_count_annotation"
        )

        posts = list(queryset)

        assert posts[0].id == self.post1.id
        assert posts[1].id == self.post2.id
        assert posts[2].id == self.post3.id

    def test_queryset_with_filtering(self):
        queryset = BlogPost.objects.with_comments_count_annotation().filter(
            comments_count_annotation__gt=0
        )

        assert queryset.count() == 2
        assert self.post1 in queryset
        assert self.post2 in queryset
        assert self.post3 not in queryset

    def test_distinct_behavior_with_annotations(self):
        self.post1.likes.add(UserAccountFactory())
        BlogCommentFactory(
            post=self.post1, user=UserAccountFactory(), approved=True
        )

        queryset = (
            BlogPost.objects.with_likes_count_annotation()
            .with_comments_count_annotation()
            .with_tags_count_annotation()
        )

        post_ids = list(queryset.values_list("id", flat=True))
        assert len(post_ids) == len(set(post_ids))

    def test_queryset_returns_correct_type(self):
        queryset = BlogPost.objects.filter().with_likes_count_annotation()

        assert isinstance(queryset, BlogPostQuerySet)

        chained = queryset.with_comments_count_annotation()
        assert isinstance(chained, BlogPostQuerySet)

    def test_approved_comments_filtering(self):
        queryset = BlogPost.objects.with_comments_count_annotation()
        post1_annotated = queryset.get(id=self.post1.id)

        assert post1_annotated.comments_count_annotation == 2

        assert (
            post1_annotated.comments_count_annotation
            == self.post1.comments_count
        )
        assert self.post1.all_comments_count == 3

    def test_active_tags_filtering(self):
        queryset = BlogPost.objects.with_tags_count_annotation()
        post1_annotated = queryset.get(id=self.post1.id)

        assert post1_annotated.tags_count_annotation == 2

        assert post1_annotated.tags_count_annotation == self.post1.tags_count


class TestBlogPostManagerEdgeCases(TestCase):
    def test_empty_queryset_behavior(self):
        assert BlogPost.objects.with_likes_count_annotation().count() == 0
        assert BlogPost.objects.with_comments_count_annotation().count() == 0
        assert BlogPost.objects.with_tags_count_annotation().count() == 0

    def test_posts_without_relations(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)

        likes_queryset = BlogPost.objects.with_likes_count_annotation()
        comments_queryset = BlogPost.objects.with_comments_count_annotation()
        tags_queryset = BlogPost.objects.with_tags_count_annotation()

        likes_post = likes_queryset.get(id=post.id)
        comments_post = comments_queryset.get(id=post.id)
        tags_post = tags_queryset.get(id=post.id)

        assert likes_post.likes_count_annotation == 0
        assert comments_post.comments_count_annotation == 0
        assert tags_post.tags_count_annotation == 0

    def test_posts_with_inactive_tags_only(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)

        inactive_tag1 = BlogTagFactory(active=False)
        inactive_tag2 = BlogTagFactory(active=False)
        post.tags.add(inactive_tag1, inactive_tag2)

        annotated_posts = BlogPost.objects.with_tags_count_annotation()
        annotated_post = annotated_posts.get(id=post.id)

        assert annotated_post.tags_count_annotation == 0

    def test_posts_with_unapproved_comments_only(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)

        BlogCommentFactory(post=post, user=UserAccountFactory(), approved=False)
        BlogCommentFactory(post=post, user=UserAccountFactory(), approved=False)

        annotated_posts = BlogPost.objects.with_comments_count_annotation()
        annotated_post = annotated_posts.get(id=post.id)

        assert annotated_post.comments_count_annotation == 0
        assert post.all_comments_count == 2

    def test_large_dataset_performance(self):
        author = BlogAuthorFactory(user=UserAccountFactory())

        posts = []
        for i in range(20):
            post = BlogPostFactory(author=author)
            posts.append(post)

            for j in range(i % 5):
                user = UserAccountFactory()
                post.likes.add(user)
                BlogCommentFactory(post=post, user=user, approved=j % 2 == 0)

                if j < 3:
                    tag = BlogTagFactory(active=True)
                    post.tags.add(tag)

        likes_queryset = BlogPost.objects.with_likes_count_annotation()
        comments_queryset = BlogPost.objects.with_comments_count_annotation()
        tags_queryset = BlogPost.objects.with_tags_count_annotation()

        assert likes_queryset.count() == 20
        assert comments_queryset.count() == 20
        assert tags_queryset.count() == 20

        for post in likes_queryset:
            assert hasattr(post, "likes_count_annotation")

        for post in comments_queryset:
            assert hasattr(post, "comments_count_annotation")

        for post in tags_queryset:
            assert hasattr(post, "tags_count_annotation")

    def test_annotation_with_deleted_relations(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)

        user = UserAccountFactory()
        post.likes.add(user)
        comment = BlogCommentFactory(post=post, user=user, approved=True)
        tag = BlogTagFactory(active=True)
        post.tags.add(tag)

        likes_post = BlogPost.objects.with_likes_count_annotation().get(
            id=post.id
        )
        comments_post = BlogPost.objects.with_comments_count_annotation().get(
            id=post.id
        )
        tags_post = BlogPost.objects.with_tags_count_annotation().get(
            id=post.id
        )

        assert likes_post.likes_count_annotation == 1
        assert comments_post.comments_count_annotation == 1
        assert tags_post.tags_count_annotation == 1

        comment.delete()
        tag.delete()
        post.likes.remove(user)

        likes_post = BlogPost.objects.with_likes_count_annotation().get(
            id=post.id
        )
        comments_post = BlogPost.objects.with_comments_count_annotation().get(
            id=post.id
        )
        tags_post = BlogPost.objects.with_tags_count_annotation().get(
            id=post.id
        )

        assert likes_post.likes_count_annotation == 0
        assert comments_post.comments_count_annotation == 0
        assert tags_post.tags_count_annotation == 0

    def test_manager_get_queryset_method(self):
        manager = BlogPost.objects
        queryset = manager.get_queryset()

        assert isinstance(queryset, BlogPostQuerySet)

        assert hasattr(queryset, "with_likes_count_annotation")
        assert hasattr(queryset, "with_comments_count_annotation")
        assert hasattr(queryset, "with_tags_count_annotation")

    def test_mixed_approval_status_comments(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)

        BlogCommentFactory(post=post, user=UserAccountFactory(), approved=True)
        BlogCommentFactory(post=post, user=UserAccountFactory(), approved=True)
        BlogCommentFactory(post=post, user=UserAccountFactory(), approved=False)
        BlogCommentFactory(post=post, user=UserAccountFactory(), approved=False)
        BlogCommentFactory(post=post, user=UserAccountFactory(), approved=True)

        annotated_post = BlogPost.objects.with_comments_count_annotation().get(
            id=post.id
        )

        assert annotated_post.comments_count_annotation == 3

        assert post.comments_count == 3
        assert post.all_comments_count == 5

    def test_mixed_active_status_tags(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)

        active_tag1 = BlogTagFactory(active=True)
        active_tag2 = BlogTagFactory(active=True)
        inactive_tag1 = BlogTagFactory(active=False)
        inactive_tag2 = BlogTagFactory(active=False)

        post.tags.add(active_tag1, active_tag2, inactive_tag1, inactive_tag2)

        annotated_post = BlogPost.objects.with_tags_count_annotation().get(
            id=post.id
        )

        assert annotated_post.tags_count_annotation == 2

        assert post.tags_count == 2
