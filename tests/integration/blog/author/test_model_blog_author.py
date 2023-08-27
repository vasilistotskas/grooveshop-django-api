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
        # Create a sample BlogAuthor instance for testing
        self.author = BlogAuthor.objects.create(
            user=self.user, website="http://example.com"
        )
        for language in languages:
            self.author.set_current_language(language)
            self.author.bio = f"Bio of {self.user.email} in {language}"
            self.author.save()
        self.author.set_current_language(default_language)

    def test_fields(self):
        # Test if the fields are saved correctly
        self.assertEqual(self.author.user, self.user)
        self.assertEqual(self.author.website, "http://example.com")

    def test_verbose_names(self):
        # Test verbose names for fields
        self.assertEqual(
            BlogAuthor._meta.get_field("user").verbose_name,
            "user",
        )
        self.assertEqual(
            BlogAuthor._meta.get_field("website").verbose_name,
            "Website",
        )

    def test_meta_verbose_names(self):
        # Test verbose names from the Meta class
        self.assertEqual(
            BlogAuthor._meta.verbose_name,
            "Blog Author",
        )
        self.assertEqual(
            BlogAuthor._meta.verbose_name_plural,
            "Blog Authors",
        )

    def test_unicode_representation(self):
        # Test the __unicode__ method returns the translated name
        self.assertEqual(str(self.author), self.author.user.email)

    def test_translations(self):
        # Test if translations are saved correctly
        for language in languages:
            self.author.set_current_language(language)
            self.assertEqual(
                self.author.bio,
                f"Bio of {self.user.email} in {language}",
            )

    def test_str_representation(self):
        # Test the __str__ method returns the user's email
        self.assertEqual(str(self.author), self.user.email)

    def tearDown(self) -> None:
        super().tearDown()
        self.author.delete()
        self.user.delete()
