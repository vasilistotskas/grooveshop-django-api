"""
Tests for CONTAINS operator support in IndexQuerySet.

Tests that the IndexQuerySet correctly handles __contains lookup
for substring matching using Meilisearch's experimental CONTAINS operator.
"""

from unittest.mock import MagicMock, patch

import pytest

from meili.querysets import IndexQuerySet


class MockModel:
    """Mock model for testing IndexQuerySet."""

    __name__ = "MockModel"
    _meilisearch = {
        "index_name": "test_index",
        "supports_geo": False,
    }

    class MeiliMeta:
        primary_key = "id"

    class objects:
        @staticmethod
        def filter(**kwargs):
            return MagicMock()


class TestContainsOperator:
    """Test suite for CONTAINS operator functionality."""

    def setup_method(self):
        """Set up test fixtures."""
        self.mock_index = MagicMock()
        self.mock_index.get_stats.return_value = MagicMock(
            number_of_documents=100
        )

    @pytest.mark.parametrize(
        "field,value,expected_filter",
        [
            ("name", "laptop", 'name CONTAINS "laptop"'),
            ("title", "gaming", 'title CONTAINS "gaming"'),
            ("description", "wireless", 'description CONTAINS "wireless"'),
            ("category", "electronics", 'category CONTAINS "electronics"'),
            ("brand", "Apple", 'brand CONTAINS "Apple"'),
            ("sku", "ABC123", 'sku CONTAINS "ABC123"'),
            ("tags", "sale", 'tags CONTAINS "sale"'),
            ("content", "tutorial", 'content CONTAINS "tutorial"'),
        ],
    )
    @patch("meili.querysets.client")
    def test_contains_filter_generation(
        self, mock_client, field, value, expected_filter
    ):
        """Test CONTAINS filter generation for various fields."""
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        queryset.filter(**{f"{field}__contains": value})

        assert expected_filter in queryset.filters

    @pytest.mark.parametrize(
        "value,expected_filter",
        [
            ("laptop", 'name CONTAINS "laptop"'),
            ("Laptop", 'name CONTAINS "Laptop"'),
            ("LAPTOP", 'name CONTAINS "LAPTOP"'),
            ("LaPtOp", 'name CONTAINS "LaPtOp"'),
        ],
    )
    @patch("meili.querysets.client")
    def test_contains_case_insensitive_matching(
        self, mock_client, value, expected_filter
    ):
        """Test CONTAINS filter preserves case (Meilisearch handles case-insensitivity)."""
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        queryset.filter(name__contains=value)

        assert expected_filter in queryset.filters

    @pytest.mark.parametrize(
        "field,invalid_value,expected_type",
        [
            ("price", 100, "int"),
            ("stock", 50, "int"),
            ("rating", 4.5, "float"),
            ("discount", 0.25, "float"),
            ("is_active", True, "bool"),
            ("tags", ["tag1", "tag2"], "list"),
            ("metadata", {"key": "value"}, "dict"),
            ("count", None, "NoneType"),
        ],
    )
    @patch("meili.querysets.client")
    def test_contains_type_validation(
        self, mock_client, field, invalid_value, expected_type
    ):
        """Test CONTAINS operator rejects non-string values with descriptive error."""
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)

        with pytest.raises(TypeError) as exc_info:
            queryset.filter(**{f"{field}__contains": invalid_value})

        error_message = str(exc_info.value)
        assert "CONTAINS operator only supports string values" in error_message
        assert expected_type in error_message
        assert field in error_message

    @pytest.mark.parametrize(
        "invalid_value,expected_type",
        [
            (123, "int"),
            (45.67, "float"),
            (True, "bool"),
            (False, "bool"),
            ([], "list"),
            ({}, "dict"),
            (None, "NoneType"),
            (("tuple",), "tuple"),
        ],
    )
    @patch("meili.querysets.client")
    def test_contains_error_handling(
        self, mock_client, invalid_value, expected_type
    ):
        """Test CONTAINS operator provides descriptive error messages."""
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)

        with pytest.raises(TypeError) as exc_info:
            queryset.filter(name__contains=invalid_value)

        error_message = str(exc_info.value)
        assert "CONTAINS operator only supports string values" in error_message
        assert expected_type in error_message
        assert "name" in error_message
        assert "string field" in error_message

    @pytest.mark.parametrize(
        "substring",
        [
            "a",
            "ab",
            "laptop",
            "gaming laptop",
            "MacBook Pro",
            "ABC-123",
            "test@example",
            "product_name",
            "",
        ],
    )
    @patch("meili.querysets.client")
    def test_contains_various_substring_values(self, mock_client, substring):
        """Test CONTAINS operator with various substring values."""
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        queryset.filter(name__contains=substring)

        expected_filter = f'name CONTAINS "{substring}"'
        assert expected_filter in queryset.filters

    @patch("meili.querysets.client")
    def test_contains_with_multiple_filters(self, mock_client):
        """Test CONTAINS operator combined with other filters."""
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        queryset.filter(
            name__contains="laptop",
            category="electronics",
            price__gte=500,
        )

        assert 'name CONTAINS "laptop"' in queryset.filters
        assert "category = 'electronics'" in queryset.filters
        assert "price >= 500" in queryset.filters

    @patch("meili.querysets.client")
    def test_contains_filter_chaining(self, mock_client):
        """Test CONTAINS operator with method chaining."""
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(name__contains="laptop").filter(
            brand__contains="Apple"
        )

        assert 'name CONTAINS "laptop"' in result.filters
        assert 'brand CONTAINS "Apple"' in result.filters

    @pytest.mark.parametrize(
        "special_string",
        [
            'test"quote',
            "test'quote",
            "test\\backslash",
            "test\nnewline",
            "test\ttab",
        ],
    )
    @patch("meili.querysets.client")
    def test_contains_with_special_characters(
        self, mock_client, special_string
    ):
        """Test CONTAINS operator with special characters in substring."""
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        queryset.filter(name__contains=special_string)

        expected_filter = f'name CONTAINS "{special_string}"'
        assert expected_filter in queryset.filters

    @patch("meili.querysets.client")
    def test_contains_preserves_other_lookups(self, mock_client):
        """Test that CONTAINS support doesn't break existing lookups."""
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)

        queryset.filter(
            name="exact_match",
            price__gte=100,
            stock__lte=50,
            category__in=["electronics", "computers"],
            is_active__isnull=False,
        )

        assert "name = 'exact_match'" in queryset.filters
        assert "price >= 100" in queryset.filters
        assert "stock <= 50" in queryset.filters
        assert "category IN ['electronics', 'computers']" in queryset.filters
        assert "is_active NOT IS NULL" in queryset.filters
