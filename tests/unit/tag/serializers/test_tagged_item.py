import unittest
from unittest.mock import MagicMock, Mock, patch

from django.contrib.contenttypes.models import ContentType

from product.models import Product
from tag.models import Tag, TaggedItem
from tag.serializers.tagged_item import TaggedItemSerializer


class TestTaggedItemSerializer(unittest.TestCase):
    def setUp(self):
        self.tag = Mock(spec=Tag)
        self.tag.id = 1
        self.tag.active = True
        self.tag.sort_order = 1
        self.tag.created_at = "2023-01-01T00:00:00Z"
        self.tag.updated_at = "2023-01-01T00:00:00Z"
        self.tag.uuid = "12345678-1234-5678-1234-567812345678"

        translations_mock = MagicMock()
        translations_mock.all.return_value = [
            Mock(language_code="en", label="Test Tag")
        ]
        self.tag.translations = translations_mock

        self.product = Mock(spec=Product)
        self.product.id = 1

        self.content_type = Mock(spec=ContentType)
        self.content_type.id = 1

        self.tagged_item = Mock(spec=TaggedItem)
        self.tagged_item.id = 1
        self.tagged_item.tag = self.tag
        self.tagged_item.content_type = self.content_type
        self.tagged_item.object_id = self.product.id
        self.tagged_item.content_object = self.product
        self.tagged_item.created_at = "2023-01-01T00:00:00Z"
        self.tagged_item.updated_at = "2023-01-01T00:00:00Z"
        self.tagged_item.uuid = "87654321-8765-4321-8765-432187654321"

    @patch("tag.serializers.tag.TagSerializer")
    @patch("core.api.serializers.ContentObjectRelatedField.to_representation")
    def test_serializer_contains_expected_fields(
        self, mock_content_object_to_representation, mock_tag_serializer
    ):
        mock_tag_serializer.return_value.data = {
            "id": self.tag.id,
            "translations": {"en": {"label": "Test Tag"}},
            "active": self.tag.active,
            "sort_order": self.tag.sort_order,
            "created_at": self.tag.created_at,
            "updated_at": self.tag.updated_at,
            "uuid": self.tag.uuid,
        }

        mock_content_object_to_representation.return_value = {
            "id": self.product.id,
            "name": "Test Product",
        }

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

        self.assertEqual(data["tag"], mock_tag_serializer.return_value.data)

        self.assertEqual(
            data["content_object"],
            mock_content_object_to_representation.return_value,
        )
