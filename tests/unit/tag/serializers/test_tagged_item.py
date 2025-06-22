from django.test import TestCase

from product.models import Product
from tag.models import Tag, TaggedItem
from tag.serializers.tagged_item import TaggedItemSerializer


class TestTaggedItemSerializer(TestCase):
    def setUp(self):
        self.tag = Tag.objects.create(active=True, sort_order=1)
        self.tag.set_current_language("en")
        self.tag.label = "Test Tag"
        self.tag.save()

        self.product = Product.objects.create(price=10.00, stock=5)
        self.product.set_current_language("en")
        self.product.name = "Test Product"
        self.product.save()

        self.tagged_item = TaggedItem.objects.create(
            tag=self.tag, content_object=self.product
        )

    def test_serializer_contains_expected_fields(self):
        serializer = TaggedItemSerializer(self.tagged_item)
        data = serializer.data

        expected_fields = [
            "id",
            "tag",
            "content_type",
            "object_id",
            "content_object",
            "created_at",
            "updated_at",
            "uuid",
        ]

        for field in expected_fields:
            self.assertIn(field, data)

        self.assertEqual(data["tag"]["id"], self.tag.id)
        self.assertTrue(data["tag"]["active"])

        self.assertEqual(data["content_object"]["id"], self.product.id)
        self.assertEqual(data["object_id"], self.product.id)
