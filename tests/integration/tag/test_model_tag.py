from django.conf import settings
from django.test import TestCase

from tag.factories.tag import TagFactory
from tag.factories.tagged_item import TaggedProductFactory
from tag.models.tag import Tag
from product.factories.product import ProductFactory

languages = [
    lang["code"] for lang in settings.PARLER_LANGUAGES[settings.SITE_ID]
]
default_language = settings.PARLER_DEFAULT_LANGUAGE_CODE


class TagModelTestCase(TestCase):
    def setUp(self):
        self.tag = TagFactory(active=True)
        self.tag.sort_order = 0

    def test_fields(self):
        self.assertTrue(self.tag.active)

    def test_str_representation(self):
        tag_label = (
            self.tag.safe_translation_getter("label", any_language=True)
            or "Unnamed Label"
        )
        self.assertEqual(
            str(self.tag),
            f"{tag_label} ({'Active' if self.tag.active else 'Inactive'})",
        )

    def test_get_ordering_queryset(self):
        Tag.objects.all().delete()

        tags = [
            TagFactory(sort_order=1, active=True),
            TagFactory(sort_order=2, active=True),
            TagFactory(sort_order=3, active=True),
        ]

        for tag in tags:
            tag.save()

        ordered_tags = Tag.objects.all().order_by("sort_order")

        ordered_sort_orders = [tag.sort_order for tag in ordered_tags]

        self.assertEqual(ordered_sort_orders, [1, 2, 3])

    def test_get_usage_count(self):
        self.assertEqual(self.tag.get_usage_count(), 0)

        product1 = ProductFactory()
        product2 = ProductFactory()
        TaggedProductFactory(tag=self.tag, content_object=product1)
        TaggedProductFactory(tag=self.tag, content_object=product2)

        self.assertEqual(self.tag.get_usage_count(), 2)

    def test_get_content_types(self):
        self.assertEqual(self.tag.get_content_types(), "")

        product = ProductFactory()
        TaggedProductFactory(tag=self.tag, content_object=product)

        content_types = self.tag.get_content_types()
        self.assertIn("product.product", content_types)

    def test_active_manager_method(self):
        active_tags = Tag.objects.active()
        self.assertIn(self.tag, active_tags)

        inactive_tag = TagFactory(active=False)
        active_tags = Tag.objects.active()
        self.assertNotIn(inactive_tag, active_tags)

    def test_inactive_manager_method(self):
        inactive_tag = TagFactory(active=False)
        inactive_tags = Tag.objects.inactive()
        self.assertIn(inactive_tag, inactive_tags)

        self.assertNotIn(self.tag, inactive_tags)

    def test_translatable_fields(self):
        for language in languages:
            self.tag.set_current_language(language)
            self.tag.label = f"Test label in {language}"
            self.tag.save()

        for language in languages:
            self.tag.set_current_language(language)
            self.assertIsNotNone(self.tag.label)

    def test_uuid_field(self):
        self.assertIsNotNone(self.tag.uuid)

        other_tag = TagFactory()
        self.assertNotEqual(self.tag.uuid, other_tag.uuid)

    def test_timestamp_fields(self):
        self.assertIsNotNone(self.tag.created_at)
        self.assertIsNotNone(self.tag.updated_at)

        original_updated_at = self.tag.updated_at
        self.tag.active = False
        self.tag.save()

        self.assertNotEqual(original_updated_at, self.tag.updated_at)

    def test_sort_order_default(self):
        new_tag = TagFactory()
        self.assertIsNotNone(new_tag.sort_order)
        self.assertGreaterEqual(new_tag.sort_order, 0)
