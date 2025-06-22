from django.conf import settings
from django.test import TestCase

from blog.factories.post import BlogPostFactory
from blog.factories.tag import BlogTagFactory
from blog.models.tag import BlogTag

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class BlogTagModelTestCase(TestCase):
    def setUp(self):
        self.tag = BlogTagFactory(active=True)
        self.tag.sort_order = 0

    def test_fields(self):
        self.assertTrue(self.tag.active)

    def test_str_representation(self):
        tag_name = (
            self.tag.safe_translation_getter("name", any_language=True)
            or "Unnamed Tag"
        )
        self.assertEqual(
            str(self.tag),
            f"{tag_name} ({'Active' if self.tag.active else 'Inactive'})",
        )

    def test_get_ordering_queryset(self):
        BlogTag.objects.all().delete()

        tags = [
            BlogTagFactory(sort_order=1, active=True),
            BlogTagFactory(sort_order=2, active=True),
            BlogTagFactory(sort_order=3, active=True),
        ]

        for tag in tags:
            tag.save()

        ordered_tags = BlogTag.objects.all().order_by("sort_order")

        ordered_sort_orders = [tag.sort_order for tag in ordered_tags]

        self.assertEqual(ordered_sort_orders, [1, 2, 3])

    def test_get_tag_posts_count(self):
        post = BlogPostFactory(
            num_tags=0,
            num_comments=0,
        )
        post.tags.set([self.tag])
        self.assertEqual(self.tag.get_posts_count, 1)
