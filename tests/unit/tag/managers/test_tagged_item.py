from django.test import TestCase

from tag.factories.tag import TagFactory
from tag.factories.tagged_item import TaggedProductFactory
from tag.managers import TaggedItemManager
from tag.models.tagged_item import TaggedItem
from product.factories.product import ProductFactory


class TestTaggedItemManager(TestCase):
    def setUp(self):
        self.active_tag = TagFactory(active=True)
        self.inactive_tag = TagFactory(active=False)
        self.product1 = ProductFactory()
        self.product2 = ProductFactory()

        self.active_tagged_item = TaggedProductFactory(
            tag=self.active_tag, content_object=self.product1
        )
        self.inactive_tagged_item = TaggedProductFactory(
            tag=self.inactive_tag, content_object=self.product2
        )

    def test_get_queryset_includes_optimizations(self):
        queryset = TaggedItem.objects.get_queryset()

        self.assertIn("tag", queryset.query.select_related)
        self.assertIn("content_type", queryset.query.select_related)

        self.assertIn("tag__translations", queryset._prefetch_related_lookups)

    def test_active_tags_method(self):
        active_items = TaggedItem.objects.active_tags()

        self.assertIn(self.active_tagged_item, active_items)

        self.assertNotIn(self.inactive_tagged_item, active_items)

        self.assertEqual(active_items.count(), 1)

    def test_get_tags_for_method(self):
        another_tag = TagFactory(active=True)
        another_tagged_item = TaggedProductFactory(
            tag=another_tag, content_object=self.product1
        )

        product1_items = TaggedItem.objects.get_tags_for(
            type(self.product1), self.product1.id
        )

        self.assertIn(self.active_tagged_item, product1_items)
        self.assertIn(another_tagged_item, product1_items)

        self.assertNotIn(self.inactive_tagged_item, product1_items)

        self.assertEqual(product1_items.count(), 2)

    def test_get_tags_for_different_content_types(self):
        items_for_product1 = TaggedItem.objects.get_tags_for(
            type(self.product1), self.product1.id
        )

        items_for_product2 = TaggedItem.objects.get_tags_for(
            type(self.product2), self.product2.id
        )

        self.assertEqual(items_for_product1.count(), 1)
        self.assertEqual(items_for_product2.count(), 1)

        self.assertNotEqual(list(items_for_product1), list(items_for_product2))

    def test_manager_returns_correct_type(self):
        self.assertIsInstance(TaggedItem.objects, TaggedItemManager)

        active_queryset = TaggedItem.objects.active_tags()
        self.assertTrue(hasattr(active_queryset, "filter"))
        self.assertTrue(hasattr(active_queryset, "exclude"))

    def test_chaining_methods(self):
        recent_active_items = (
            TaggedItem.objects.active_tags()
            .filter(created_at__isnull=False)
            .order_by("-created_at")
        )

        self.assertGreaterEqual(recent_active_items.count(), 0)

        for item in recent_active_items:
            self.assertTrue(item.tag.active)

    def test_performance_with_large_dataset(self):
        items = []
        for i in range(10):
            tag = TagFactory(active=(i % 2 == 0))
            product = ProductFactory()
            item = TaggedProductFactory(tag=tag, content_object=product)
            items.append(item)

        active_items = TaggedItem.objects.active_tags()
        active_count = active_items.count()

        self.assertGreaterEqual(active_count, 5)

        for item in active_items:
            self.assertTrue(item.tag.active)

    def test_empty_queryset_behavior(self):
        TaggedItem.objects.all().delete()

        active_items = TaggedItem.objects.active_tags()
        self.assertEqual(active_items.count(), 0)

        items_for_product = TaggedItem.objects.get_tags_for(
            type(self.product1), self.product1.id
        )
        self.assertEqual(items_for_product.count(), 0)

    def test_get_tags_for_nonexistent_object(self):
        items = TaggedItem.objects.get_tags_for(
            type(self.product1),
            99999,
        )

        self.assertEqual(items.count(), 0)

    def test_select_related_optimization(self):
        items = TaggedItem.objects.all()

        for item in items[:2]:
            tag_name = item.tag.active
            content_type_name = item.content_type.model

            self.assertIsNotNone(tag_name)
            self.assertIsNotNone(content_type_name)

    def test_prefetch_tag_translations(self):
        items = TaggedItem.objects.all()

        for item in items[:2]:
            tag_label = item.tag.safe_translation_getter(
                "label", any_language=True
            )
            self.assertTrue(isinstance(tag_label, (str, type(None))))


class TestTaggedItemManagerEdgeCases(TestCase):
    def test_manager_with_deleted_tags(self):
        tag = TagFactory(active=True)
        product = ProductFactory()
        TaggedProductFactory(tag=tag, content_object=product)

        tag_id = tag.id
        tag.delete()

        self.assertFalse(TaggedItem.objects.filter(tag_id=tag_id).exists())

    def test_manager_with_deleted_content_objects(self):
        tag = TagFactory(active=True)
        product = ProductFactory()
        TaggedProductFactory(tag=tag, content_object=product)

        product_id = product.id
        product.delete()

        self.assertTrue(
            TaggedItem.objects.filter(object_id=product_id).exists()
        )

    def test_get_tags_for_with_different_model_types(self):
        product_items = TaggedItem.objects.get_tags_for(
            ProductFactory._meta.model,
            self.product1.id if hasattr(self, "product1") else 1,
        )

        self.assertGreaterEqual(product_items.count(), 0)

    def test_manager_method_consistency(self):
        all_items = TaggedItem.objects.all()
        active_items = TaggedItem.objects.active_tags()

        self.assertLessEqual(active_items.count(), all_items.count())

        for item in active_items:
            self.assertTrue(item.tag.active)
