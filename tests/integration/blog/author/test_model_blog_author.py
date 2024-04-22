from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase

from blog.models.author import BlogAuthor

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE
User = get_user_model()


class BlogAuthorModelTestCase(TestCase):
    author: BlogAuthor = None
    user: User = None

    def setUp(self):
        self.user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        self.author = BlogAuthor.objects.create(
            user=self.user, website="http://example.com"
        )
        for language in languages:
            self.author.set_current_language(language)
            self.author.bio = f"Bio of {self.user.email} in {language}"
            self.author.save()
        self.author.set_current_language(default_language)

    def test_fields(self):
        self.assertEqual(self.author.user, self.user)
        self.assertEqual(self.author.website, "http://example.com")

    def test_unicode_representation(self):
        author_name = self.user.full_name
        self.assertEqual(
            self.author.__unicode__(), f"{author_name} ({self.user.email})"
        )

    def test_translations(self):
        for language in languages:
            self.author.set_current_language(language)
            self.assertEqual(
                self.author.bio,
                f"Bio of {self.user.email} in {language}",
            )

    def test_str_representation(self):
        author_name = self.user.full_name
        self.assertEqual(str(self.author), f"{author_name} ({self.user.email})")

    def tearDown(self) -> None:
        super().tearDown()
        self.author.delete()
        self.user.delete()
