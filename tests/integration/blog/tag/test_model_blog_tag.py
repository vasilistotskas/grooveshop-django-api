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
        self.assertTrue(self.tag.active)

    def test_unicode_representation(self):
        tag_name = (
            self.tag.safe_translation_getter("name", any_language=True) or "Unnamed Tag"
        )
        self.assertEqual(
            self.tag.__unicode__(),
            f"{tag_name} ({'Active' if self.tag.active else 'Inactive'})",
        )

    def test_translations(self):
        for language in languages:
            self.tag.set_current_language(language)
            self.assertEqual(self.tag.name, f"Tag name in {language}")

    def test_str_representation(self):
        tag_name = (
            self.tag.safe_translation_getter("name", any_language=True) or "Unnamed Tag"
        )
        self.assertEqual(
            str(self.tag),
            f"{tag_name} ({'Active' if self.tag.active else 'Inactive'})",
        )

    def test_get_ordering_queryset(self):
        tag_2 = BlogTag.objects.create()
        tag_2.sort_order = 1
        tag_2.save()
        tag_3 = BlogTag.objects.create()
        tag_3.sort_order = 2
        tag_3.save()
        tag_4 = BlogTag.objects.create()
        tag_4.sort_order = 3
        for language in languages:
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
        post = BlogPost.objects.create(
            title="Test Post",
            status="draft",
            category=None,
            featured=False,
            view_count=0,
        )
        post.tags.set([self.tag])
        self.assertEqual(self.tag.get_tag_posts_count, 1)

    def tearDown(self) -> None:
        super().tearDown()
        self.tag.delete()
