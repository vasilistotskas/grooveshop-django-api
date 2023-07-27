from django.conf import settings
from django.test import TestCase

from blog.models.author import BlogAuthor
from blog.models.comment import BlogComment
from blog.models.post import BlogPost
from user.models import UserAccount

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class BlogCommentModelTestCase(TestCase):
    comment = None
    user = None
    author = None
    post = None

    def setUp(self):
        self.user = UserAccount.objects.create(
            email="testuser@example.com", password="testpassword"
        )
        self.author = BlogAuthor.objects.create(user=self.user)
        self.post = BlogPost.objects.create(title="Test Post", author=self.author)
        self.comment = BlogComment.objects.create(
            is_approved=True, user=self.user, post=self.post
        )
        for language in languages:
            self.comment.set_current_language(language)
            self.comment.content = f"Comment Content in {language}"
            self.comment.save()
        self.comment.set_current_language(default_language)

    def test_fields(self):
        # Test if the fields are saved correctly
        self.assertTrue(self.comment.is_approved)
        self.assertEqual(self.comment.user, self.user)
        self.assertEqual(self.comment.post, self.post)

    def test_verbose_names(self):
        # Test verbose names for fields
        self.assertEqual(
            BlogComment._meta.get_field("is_approved").verbose_name,
            "Is Approved",
        )
        self.assertEqual(
            BlogComment._meta.get_field("user").verbose_name,
            "user",
        )
        self.assertEqual(
            BlogComment._meta.get_field("post").verbose_name,
            "post",
        )

    def test_meta_verbose_names(self):
        # Test verbose names from the Meta class
        self.assertEqual(
            BlogComment._meta.verbose_name,
            "Blog Comment",
        )
        self.assertEqual(
            BlogComment._meta.verbose_name_plural,
            "Blog Comments",
        )

    def test_translations(self):
        # Test if translations are saved correctly
        for language in languages:
            self.comment.set_current_language(language)
            self.assertEqual(
                self.comment.content,
                f"Comment Content in {language}",
            )

    def test_str_representation(self):
        self.assertEqual(
            str(self.comment), self.comment.safe_translation_getter("content")
        )

    def test_number_of_likes(self):
        # Create another user to simulate a "like" action
        other_user = UserAccount.objects.create(
            email="testuser2@example.com", password="testpassword"
        )

        # Initially, no likes should be present
        self.assertEqual(self.comment.number_of_likes, 0)

        # Simulate a "like" action by the other user
        self.comment.likes.add(other_user)

        # Now, there should be one like
        self.assertEqual(self.comment.number_of_likes, 1)
