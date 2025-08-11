from django.contrib.contenttypes.models import ContentType
from django.test import TestCase

from tag.factories.tag import TagFactory
from tag.factories.tagged_item import TaggedProductFactory
from tag.models.tagged_item import TaggedItem
from product.factories.product import ProductFactory


class TaggedItemModelTestCase(TestCase):
    def setUp(self):
        self.tag = TagFactory(active=True)
        self.product = ProductFactory()
        self.tagged_item = TaggedProductFactory(
            tag=self.tag, content_object=self.product
        )

    def test_fields(self):
        self.assertEqual(self.tagged_item.tag, self.tag)
        self.assertEqual(self.tagged_item.object_id, self.product.id)
        self.assertEqual(self.tagged_item.content_object, self.product)

        expected_content_type = ContentType.objects.get_for_model(self.product)
        self.assertEqual(self.tagged_item.content_type, expected_content_type)

    def test_content_object_generic_relation(self):
        self.assertIsInstance(
            self.tagged_item.content_object, type(self.product)
        )
        self.assertEqual(self.tagged_item.content_object.id, self.product.id)

    def test_manager_active_tags_method(self):
        inactive_tag = TagFactory(active=False)
        inactive_tagged_item = TaggedProductFactory(
            tag=inactive_tag, content_object=ProductFactory()
        )

        active_items = TaggedItem.objects.active_tags()

        self.assertIn(self.tagged_item, active_items)
        self.assertNotIn(inactive_tagged_item, active_items)

    def test_manager_get_tags_for_method(self):
        another_tag = TagFactory(active=True)
        TaggedProductFactory(tag=another_tag, content_object=self.product)

        product_tags = TaggedItem.objects.get_tags_for(
            type(self.product), self.product.id
        )

        self.assertEqual(product_tags.count(), 2)

    def test_uuid_field(self):
        self.assertIsNotNone(self.tagged_item.uuid)

        other_tagged_item = TaggedProductFactory(
            tag=self.tag, content_object=ProductFactory()
        )
        self.assertNotEqual(self.tagged_item.uuid, other_tagged_item.uuid)

    def test_timestamp_fields(self):
        self.assertIsNotNone(self.tagged_item.created_at)
        self.assertIsNotNone(self.tagged_item.updated_at)

    def test_cascade_delete_on_tag(self):
        tagged_item_id = self.tagged_item.id

        self.tag.delete()

        self.assertFalse(TaggedItem.objects.filter(id=tagged_item_id).exists())

    def test_cascade_delete_on_content_type(self):
        tagged_item_id = self.tagged_item.id
        content_type = self.tagged_item.content_type

        content_type.delete()

        self.assertFalse(TaggedItem.objects.filter(id=tagged_item_id).exists())


class TaggedModelTestCase(TestCase):
    def setUp(self):
        self.tag1 = TagFactory(active=True)
        self.tag2 = TagFactory(active=True)
        self.product = ProductFactory()

    def test_tag_ids_property(self):
        self.assertEqual(self.product.tag_ids, [])

        TaggedProductFactory(tag=self.tag1, content_object=self.product)
        TaggedProductFactory(tag=self.tag2, content_object=self.product)

        tag_ids = self.product.tag_ids
        self.assertIn(self.tag1.id, tag_ids)
        self.assertIn(self.tag2.id, tag_ids)
        self.assertEqual(len(tag_ids), 2)

    def test_get_tags_by_object_ids_class_method(self):
        product2 = ProductFactory()

        TaggedProductFactory(tag=self.tag1, content_object=self.product)
        TaggedProductFactory(tag=self.tag2, content_object=self.product)
        TaggedProductFactory(tag=self.tag1, content_object=product2)

        object_ids = [self.product.id, product2.id]
        tags = ProductFactory._meta.model.get_tags_by_object_ids(
            object_ids, ProductFactory._meta.model
        )

        self.assertIn(self.tag1, tags)
        self.assertIn(self.tag2, tags)

    def test_get_tags_for_object_method(self):
        TaggedProductFactory(tag=self.tag1, content_object=self.product)
        TaggedProductFactory(tag=self.tag2, content_object=self.product)

        tags = self.product.get_tags_for_object()

        self.assertIn(self.tag1, tags)
        self.assertIn(self.tag2, tags)
        self.assertEqual(tags.count(), 2)

    def test_add_tag_method(self):
        tagged_item = TaggedItem(tag=self.tag1)

        self.product.add_tag(tagged_item)

        self.assertEqual(tagged_item.content_object, self.product)
        self.assertEqual(tagged_item.object_id, self.product.id)

    def test_remove_tag_method(self):
        tagged_item = TaggedProductFactory(
            tag=self.tag1, content_object=self.product
        )
        tagged_item_id = tagged_item.id

        self.product.remove_tag(tagged_item)

        self.assertFalse(TaggedItem.objects.filter(id=tagged_item_id).exists())

    def test_clear_tags_method(self):
        TaggedProductFactory(tag=self.tag1, content_object=self.product)
        TaggedProductFactory(tag=self.tag2, content_object=self.product)

        self.assertEqual(self.product.tags.count(), 2)

        self.product.clear_tags()

        self.assertEqual(self.product.tags.count(), 0)
