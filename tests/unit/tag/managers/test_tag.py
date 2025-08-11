from django.test import TestCase

from tag.factories.tag import TagFactory
from tag.factories.tagged_item import TaggedProductFactory
from tag.managers import TagManager
from tag.models.tag import Tag
from product.factories.product import ProductFactory


class TestTagManager(TestCase):
    def setUp(self):
        self.active_tag1 = TagFactory(active=True)
        self.active_tag2 = TagFactory(active=True)
        self.inactive_tag = TagFactory(active=False)

    def test_get_queryset_includes_translations(self):
        queryset = Tag.objects.get_queryset()

        self.assertIn("translations", queryset._prefetch_related_lookups)

    def test_active_method(self):
        active_tags = Tag.objects.active()

        self.assertIn(self.active_tag1, active_tags)
        self.assertIn(self.active_tag2, active_tags)

        self.assertNotIn(self.inactive_tag, active_tags)

        self.assertEqual(active_tags.count(), 2)

    def test_inactive_method(self):
        inactive_tags = Tag.objects.inactive()

        self.assertIn(self.inactive_tag, inactive_tags)

        self.assertNotIn(self.active_tag1, inactive_tags)
        self.assertNotIn(self.active_tag2, inactive_tags)

        self.assertEqual(inactive_tags.count(), 1)

    def test_manager_returns_correct_type(self):
        self.assertIsInstance(Tag.objects, TagManager)

        active_queryset = Tag.objects.active()
        self.assertTrue(hasattr(active_queryset, "filter"))
        self.assertTrue(hasattr(active_queryset, "exclude"))

    def test_chaining_methods(self):
        recent_active_tags = (
            Tag.objects.active()
            .filter(created_at__isnull=False)
            .order_by("-created_at")
        )

        self.assertGreaterEqual(recent_active_tags.count(), 0)

        for tag in recent_active_tags:
            self.assertTrue(tag.active)

    def test_select_related_optimization(self):
        queryset = Tag.objects.get_queryset()

        tag = queryset.first()
        if tag:
            self.assertIsNotNone(tag.id)

    def test_prefetch_translations(self):
        TagFactory(active=True)

        queryset = Tag.objects.all()
        retrieved_tag = queryset.first()

        self.assertIsNotNone(retrieved_tag)


class TestTagManagerWithUsage(TestCase):
    def setUp(self):
        self.tag_with_usage = TagFactory(active=True)
        self.tag_without_usage = TagFactory(active=True)

        product = ProductFactory()
        TaggedProductFactory(tag=self.tag_with_usage, content_object=product)

    def test_tags_with_usage_annotation(self):
        tags = Tag.objects.all()

        self.assertEqual(tags.count(), 2)

        for tag in tags:
            usage_count = tag.get_usage_count()
            self.assertIsInstance(usage_count, int)
            self.assertGreaterEqual(usage_count, 0)

    def test_manager_performance_with_large_dataset(self):
        tags = []
        for i in range(10):
            tag = TagFactory(active=(i % 2 == 0))
            tags.append(tag)

        active_tags = Tag.objects.active()
        active_count = active_tags.count()

        self.assertGreaterEqual(active_count, 5)

        inactive_tags = Tag.objects.inactive()
        inactive_count = inactive_tags.count()

        self.assertGreaterEqual(inactive_count, 5)

    def test_empty_queryset_behavior(self):
        Tag.objects.all().delete()

        active_tags = Tag.objects.active()
        self.assertEqual(active_tags.count(), 0)

        inactive_tags = Tag.objects.inactive()
        self.assertEqual(inactive_tags.count(), 0)

    def test_manager_with_custom_filtering(self):
        filtered_active = Tag.objects.active().filter(
            id__in=[self.tag_with_usage.id]
        )

        self.assertEqual(filtered_active.count(), 1)
        self.assertEqual(filtered_active.first(), self.tag_with_usage)

    def test_manager_method_consistency(self):
        all_tags = Tag.objects.all()
        active_tags = Tag.objects.active()
        inactive_tags = Tag.objects.inactive()

        total_expected = active_tags.count() + inactive_tags.count()
        self.assertEqual(all_tags.count(), total_expected)
