from django.conf import settings
from django.test import TestCase

from blog.models.post import BlogPost
from blog.models.tag import BlogTag

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class BlogTagModelTestCase(TestCase):
    tag: BlogTag = None

    def setUp(self):
        self.tag = BlogTag.objects.create()
        self.tag.sort_order = 0
        for language in languages:
            self.tag.set_current_language(language)
            self.tag.name = f"Tag name in {language}"
            self.tag.save()
        self.tag.set_current_language(default_language)

    def test_fields(self):
        # Test if the fields are saved correctly
        self.assertTrue(self.tag.active)

    def test_verbose_names(self):
        # Test verbose names for fields
        self.assertEqual(BlogTag._meta.get_field("active").verbose_name, "Active")

    def test_meta_verbose_names(self):
        # Test verbose names from the Meta class
        self.assertEqual(BlogTag._meta.verbose_name, "Blog Tag")
        self.assertEqual(BlogTag._meta.verbose_name_plural, "Blog Tags")

    def test_unicode_representation(self):
        # Test the __unicode__ method returns the translated name
        self.assertEqual(
            self.tag.__unicode__(),
            self.tag.safe_translation_getter("name"),
        )

    def test_translations(self):
        # Test if translations are saved correctly
        for language in languages:
            self.tag.set_current_language(language)
            self.assertEqual(self.tag.name, f"Tag name in {language}")

    def test_str_representation(self):
        # Test the __str__ method returns the translated name
        self.assertEqual(
            str(self.tag),
            self.tag.safe_translation_getter("name"),
        )

    def test_get_ordering_queryset(self):
        # Test if the get_ordering_queryset method returns the correct queryset
        tag_2 = BlogTag.objects.create()
        tag_2.sort_order = 1
        tag_2.save()
        tag_3 = BlogTag.objects.create()
        tag_3.sort_order = 2
        tag_3.save()
        tag_4 = BlogTag.objects.create()
        tag_4.sort_order = 3
        for language in languages:
            # Set the name for each tag in each language
            tag_2.set_current_language(language)
            tag_2.name = f"Tag name in {language}"
            tag_2.save()
            tag_3.set_current_language(language)
            tag_3.name = f"Tag name in {language}"
            tag_3.save()
            tag_4.set_current_language(language)
            tag_4.name = f"Tag name in {language}"
            tag_4.save()
        tag_2.set_current_language(default_language)
        tag_3.set_current_language(default_language)
        tag_4.set_current_language(default_language)

        self.assertEqual(
            list(BlogTag.objects.all()),
            [self.tag, tag_2, tag_3, tag_4],
        )

    def test_get_tag_posts_count(self):
        # Test if the get_tag_posts_count property returns the correct count of related blog posts
        post = BlogPost.objects.create(
            status="draft",  # Set the status to "draft" for the test
            category=None,
            featured=False,
            view_count=0,
        )
        post.tags.set([self.tag])
        self.assertEqual(self.tag.get_tag_posts_count, 1)

    def tearDown(self) -> None:
        super().tearDown()
        self.tag.delete()
