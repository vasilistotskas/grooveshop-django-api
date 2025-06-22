from django.test import TestCase
from mptt.managers import TreeManager
from parler.managers import TranslatableManager

from blog.factories.author import BlogAuthorFactory
from blog.factories.comment import BlogCommentFactory
from blog.factories.post import BlogPostFactory
from blog.managers.comment import BlogCommentManager, BlogCommentQuerySet
from blog.models.comment import BlogComment
from user.factories.account import UserAccountFactory


class TestBlogCommentManager(TestCase):
    def setUp(self):
        self.author = BlogAuthorFactory(user=UserAccountFactory())
        self.post = BlogPostFactory(author=self.author)

        self.user1 = UserAccountFactory()
        self.user2 = UserAccountFactory()
        self.user3 = UserAccountFactory()
        self.user4 = UserAccountFactory()
        self.user5 = UserAccountFactory()

        self.approved_comment1 = BlogCommentFactory(
            post=self.post, user=self.user1, is_approved=True
        )
        self.approved_comment2 = BlogCommentFactory(
            post=self.post, user=self.user2, is_approved=True
        )
        self.unapproved_comment = BlogCommentFactory(
            post=self.post, user=self.user3, is_approved=False
        )

        self.reply_approved = BlogCommentFactory(
            post=self.post,
            user=self.user4,
            parent=self.approved_comment1,
            is_approved=True,
        )
        self.reply_unapproved = BlogCommentFactory(
            post=self.post,
            user=self.user5,
            parent=self.approved_comment1,
            is_approved=False,
        )

    def test_approved_manager_method(self):
        approved_comments = BlogComment.objects.approved()

        assert self.approved_comment1 in approved_comments
        assert self.approved_comment2 in approved_comments
        assert self.reply_approved in approved_comments
        assert self.unapproved_comment not in approved_comments
        assert self.reply_unapproved not in approved_comments

        assert approved_comments.count() == 3

    def test_manager_returns_correct_queryset_type(self):
        queryset = BlogComment.objects.approved()

        assert isinstance(queryset, BlogCommentQuerySet)

    def test_manager_inheritance(self):
        manager = BlogComment.objects
        assert isinstance(manager, BlogCommentManager)
        assert isinstance(manager, TreeManager)
        assert isinstance(manager, TranslatableManager)


class TestBlogCommentQuerySet(TestCase):
    def setUp(self):
        self.author = BlogAuthorFactory(user=UserAccountFactory())
        self.post = BlogPostFactory(author=self.author)

        self.user1 = UserAccountFactory()
        self.user2 = UserAccountFactory()
        self.user3 = UserAccountFactory()
        self.user4 = UserAccountFactory()
        self.user5 = UserAccountFactory()

        self.approved_comment1 = BlogCommentFactory(
            post=self.post, user=self.user1, is_approved=True
        )
        self.approved_comment2 = BlogCommentFactory(
            post=self.post, user=self.user2, is_approved=True
        )
        self.unapproved_comment = BlogCommentFactory(
            post=self.post, user=self.user3, is_approved=False
        )

        self.reply_approved = BlogCommentFactory(
            post=self.post,
            user=self.user4,
            parent=self.approved_comment1,
            is_approved=True,
        )
        self.reply_unapproved = BlogCommentFactory(
            post=self.post,
            user=self.user5,
            parent=self.approved_comment1,
            is_approved=False,
        )

    def test_approved_queryset_method(self):
        queryset = BlogComment.objects.filter().approved()

        assert self.approved_comment1 in queryset
        assert self.approved_comment2 in queryset
        assert self.reply_approved in queryset
        assert self.unapproved_comment not in queryset
        assert self.reply_unapproved not in queryset

        assert queryset.count() == 3

    def test_queryset_chaining_with_approved(self):
        queryset = BlogComment.objects.filter(user=self.user1).approved()

        assert self.approved_comment1 in queryset
        assert self.unapproved_comment not in queryset

        assert queryset.count() >= 1

    def test_queryset_with_tree_methods(self):
        queryset = BlogComment.objects.approved()

        root_comments = queryset.filter(parent__isnull=True)
        assert root_comments.count() == 2

        descendants = self.approved_comment1.get_descendants(include_self=True)
        approved_descendants = queryset.filter(
            id__in=descendants.values_list("id", flat=True)
        )
        assert approved_descendants.count() == 2

    def test_queryset_with_translations(self):
        queryset = (
            BlogComment.objects.approved()
            .exclude(translations__content__isnull=True)
            .exclude(translations__content__exact="")
        )

        assert self.approved_comment1 in queryset
        assert self.approved_comment2 in queryset
        assert self.reply_approved in queryset
        assert self.unapproved_comment not in queryset

    def test_as_manager_class_method(self):
        manager = BlogCommentQuerySet.as_manager()
        assert hasattr(manager, "_built_with_as_manager")
        assert manager._built_with_as_manager is True

    def test_queryset_returns_correct_type(self):
        queryset = BlogComment.objects.filter().approved()

        assert isinstance(queryset, BlogCommentQuerySet)

        chained = queryset.filter(user=self.user1)
        assert isinstance(chained, BlogCommentQuerySet)


class TestBlogCommentManagerTreeFunctionality(TestCase):
    def setUp(self):
        self.author = BlogAuthorFactory(user=UserAccountFactory())
        self.post = BlogPostFactory(author=self.author)

        self.user1 = UserAccountFactory()
        self.user2 = UserAccountFactory()
        self.user3 = UserAccountFactory()
        self.user4 = UserAccountFactory()
        self.user5 = UserAccountFactory()

        self.root1 = BlogCommentFactory(
            post=self.post, user=self.user1, is_approved=True
        )
        self.root2 = BlogCommentFactory(
            post=self.post, user=self.user2, is_approved=False
        )

        self.child1_1 = BlogCommentFactory(
            post=self.post, user=self.user3, parent=self.root1, is_approved=True
        )
        self.child1_2 = BlogCommentFactory(
            post=self.post,
            user=self.user4,
            parent=self.root1,
            is_approved=False,
        )

        self.grandchild1_1_1 = BlogCommentFactory(
            post=self.post,
            user=self.user5,
            parent=self.child1_1,
            is_approved=True,
        )

    def test_approved_with_tree_structure(self):
        approved_comments = BlogComment.objects.approved()

        assert self.root1 in approved_comments
        assert self.child1_1 in approved_comments
        assert self.grandchild1_1_1 in approved_comments

        assert self.root2 not in approved_comments
        assert self.child1_2 not in approved_comments

        assert approved_comments.count() == 3

    def test_tree_methods_with_approved_filter(self):
        all_descendants = self.root1.get_descendants(include_self=True)
        approved_descendants = BlogComment.objects.approved().filter(
            id__in=all_descendants.values_list("id", flat=True)
        )

        assert approved_descendants.count() == 3
        assert self.root1 in approved_descendants
        assert self.child1_1 in approved_descendants
        assert self.grandchild1_1_1 in approved_descendants
        assert self.child1_2 not in approved_descendants

    def test_tree_level_filtering_with_approved(self):
        root_approved = BlogComment.objects.approved().filter(level=0)
        assert root_approved.count() == 1
        assert self.root1 in root_approved
        assert self.root2 not in root_approved

        level1_approved = BlogComment.objects.approved().filter(level=1)
        assert level1_approved.count() == 1
        assert self.child1_1 in level1_approved
        assert self.child1_2 not in level1_approved

        level2_approved = BlogComment.objects.approved().filter(level=2)
        assert level2_approved.count() == 1
        assert self.grandchild1_1_1 in level2_approved


class TestBlogCommentManagerEdgeCases(TestCase):
    def test_empty_queryset_behavior(self):
        assert BlogComment.objects.approved().count() == 0

        queryset = BlogComment.objects.approved()
        assert list(queryset) == []

    def test_all_unapproved_comments(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)

        BlogCommentFactory(
            post=post, user=UserAccountFactory(), is_approved=False
        )
        BlogCommentFactory(
            post=post, user=UserAccountFactory(), is_approved=False
        )

        approved_comments = BlogComment.objects.approved()
        assert approved_comments.count() == 0

    def test_all_approved_comments(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)

        comment1 = BlogCommentFactory(
            post=post, user=UserAccountFactory(), is_approved=True
        )
        comment2 = BlogCommentFactory(
            post=post, user=UserAccountFactory(), is_approved=True
        )
        comment3 = BlogCommentFactory(
            post=post, user=UserAccountFactory(), is_approved=True
        )

        approved_comments = BlogComment.objects.approved()
        assert approved_comments.count() == 3
        assert comment1 in approved_comments
        assert comment2 in approved_comments
        assert comment3 in approved_comments

    def test_manager_with_deleted_posts(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)
        comment = BlogCommentFactory(
            post=post, user=UserAccountFactory(), is_approved=True
        )

        assert BlogComment.objects.approved().count() == 1

        post.delete()

        comment.refresh_from_db()
        assert comment.post is None

        approved_comments = BlogComment.objects.approved()
        assert approved_comments.count() == 1

    def test_manager_with_deleted_users(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)
        user = UserAccountFactory()
        comment = BlogCommentFactory(post=post, user=user, is_approved=True)

        assert BlogComment.objects.approved().count() == 1

        user.delete()

        comment.refresh_from_db()
        assert comment.user is None

        approved_comments = BlogComment.objects.approved()
        assert approved_comments.count() == 1

    def test_queryset_class_attribute(self):
        manager = BlogComment.objects
        assert isinstance(manager, BlogCommentManager)
        assert manager._queryset_class == BlogCommentQuerySet

    def test_performance_with_large_tree(self):
        author = BlogAuthorFactory(user=UserAccountFactory())
        post = BlogPostFactory(author=author)

        root_comments = []
        for i in range(5):
            root = BlogCommentFactory(
                post=post,
                user=UserAccountFactory(),
                is_approved=i % 2 == 0,
            )
            root_comments.append(root)

            for j in range(3):
                child = BlogCommentFactory(
                    post=post,
                    user=UserAccountFactory(),
                    parent=root,
                    is_approved=j % 2 == 0,
                )

                for k in range(2):
                    BlogCommentFactory(
                        post=post,
                        user=UserAccountFactory(),
                        parent=child,
                        is_approved=k % 2 == 0,
                    )

        approved_comments = BlogComment.objects.approved()

        assert approved_comments.count() > 0

        for comment in approved_comments:
            assert comment.is_approved is True
