from django.conf import settings
from django.test import TestCase

from blog.models.post import BlogPost
from blog.models.tag import BlogTag

languages = [lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class BlogTagModelTestCase(TestCase):
    blog_tag = None

    def setUp(self):
        self.blog_tag = BlogTag.objects.create()
        self.blog_tag.sort_order = 0
        for language in languages:
            self.blog_tag.set_current_language(language)
            self.blog_tag.name = f"Tag name in {language}"
            self.blog_tag.save()
        self.blog_tag.set_current_language(default_language)

    def test_fields(self):
        # Test if the fields are saved correctly
        self.assertTrue(self.blog_tag.active)

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
            self.blog_tag.__unicode__(),
            self.blog_tag.safe_translation_getter("name"),
        )

    def test_translations(self):
        # Test if translations are saved correctly
        for language in languages:
            self.blog_tag.set_current_language(language)
            self.assertEqual(self.blog_tag.name, f"Tag name in {language}")

    def test_str_representation(self):
        # Test the __str__ method returns the translated name
        self.assertEqual(
            str(self.blog_tag),
            self.blog_tag.safe_translation_getter("name"),
        )

    def test_get_ordering_queryset(self):
        # Test if the get_ordering_queryset method returns the correct queryset
        blog_tag_2 = BlogTag.objects.create()
        blog_tag_2.sort_order = 1
        blog_tag_2.save()
        blog_tag_3 = BlogTag.objects.create()
        blog_tag_3.sort_order = 2
        blog_tag_3.save()
        blog_tag_4 = BlogTag.objects.create()
        blog_tag_4.sort_order = 3
        for language in languages:
            # Set the name for each tag in each language
            blog_tag_2.set_current_language(language)
            blog_tag_2.name = f"Tag name in {language}"
            blog_tag_2.save()
            blog_tag_3.set_current_language(language)
            blog_tag_3.name = f"Tag name in {language}"
            blog_tag_3.save()
            blog_tag_4.set_current_language(language)
            blog_tag_4.name = f"Tag name in {language}"
            blog_tag_4.save()
        blog_tag_2.set_current_language(default_language)
        blog_tag_3.set_current_language(default_language)
        blog_tag_4.set_current_language(default_language)

        self.assertEqual(
            list(BlogTag.objects.all()),
            [self.blog_tag, blog_tag_2, blog_tag_3, blog_tag_4],
        )

    def test_get_tag_posts_count(self):
        # Test if the get_tag_posts_count property returns the correct count of related blog posts
        post = BlogPost.objects.create(
            status="draft",  # Set the status to "draft" for the test
            category=None,
            featured=False,
            view_count=0,
        )
        post.tags.set([self.blog_tag])
        self.assertEqual(self.blog_tag.get_tag_posts_count, 1)
