from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from blog.factories.author import BlogAuthorFactory
from blog.models.author import BlogAuthor
from user.factories.account import UserAccountFactory

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class BlogAuthorModelTestCase(TestCase):
    author: BlogAuthor = None
    user: User = None

    def setUp(self):
        self.user = UserAccountFactory(num_addresses=0)
        self.author = BlogAuthorFactory(user=self.user, website="http://example.com")

    def test_fields(self):
        self.assertEqual(self.author.user, self.user)
        self.assertEqual(self.author.website, "http://example.com")

    def test_unicode_representation(self):
        author_name = self.user.full_name
        self.assertEqual(self.author.__unicode__(), f"{author_name} ({self.user.email})")

    def test_str_representation(self):
        author_name = self.user.full_name
        self.assertEqual(str(self.author), f"{author_name} ({self.user.email})")

    def tearDown(self) -> None:
        BlogAuthor.objects.all().delete()
        User.objects.all().delete()
        super().tearDown()
