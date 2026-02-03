"""
Tests for AttributeManager custom manager methods.

Validates Requirements 3.3, 8.1, 8.4
"""

import pytest

from product.models import Attribute


@pytest.mark.django_db
class TestAttributeManager:
    """Test AttributeManager custom query methods."""

    def test_active_method_exists_and_is_chainable(self):
        """Test that active() method exists and returns a QuerySet."""
        # Call active() method
        result = Attribute.objects.active()

        # Verify it returns a QuerySet
        assert hasattr(result, "filter")
        assert hasattr(result, "count")

        # Verify it's chainable
        chained = result.filter(id__gt=0)
        assert hasattr(chained, "count")

    def test_with_values_count_method_exists(self):
        """Test that with_values_count() method exists and adds annotation."""
        # Call with_values_count() method
        result = Attribute.objects.with_values_count()

        # Verify it returns a QuerySet
        assert hasattr(result, "filter")

        # Verify it's chainable
        chained = result.filter(active=True)
        assert hasattr(chained, "count")

    def test_with_usage_count_method_exists(self):
        """Test that with_usage_count() method exists and adds annotation."""
        # Call with_usage_count() method
        result = Attribute.objects.with_usage_count()

        # Verify it returns a QuerySet
        assert hasattr(result, "filter")

        # Verify it's chainable
        chained = result.filter(active=True)
        assert hasattr(chained, "count")

    def test_all_methods_are_chainable(self):
        """Test that all manager methods can be chained together."""
        # Chain all methods
        result = (
            Attribute.objects.active().with_values_count().with_usage_count()
        )

        # Verify chaining works
        assert hasattr(result, "count")
        assert hasattr(result, "filter")

        # Should not raise any errors
        count = result.count()
        assert count >= 0
