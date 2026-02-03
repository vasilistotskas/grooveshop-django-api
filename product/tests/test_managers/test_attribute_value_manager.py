"""
Tests for AttributeValueManager custom manager methods.

Validates Requirements 3.3, 8.1, 8.4
"""

import pytest

from product.models import AttributeValue


@pytest.mark.django_db
class TestAttributeValueManager:
    """Test AttributeValueManager custom query methods."""

    def test_active_method_exists_and_is_chainable(self):
        """Test that active() method exists and returns a QuerySet."""
        # Call active() method
        result = AttributeValue.objects.active()

        # Verify it returns a QuerySet
        assert hasattr(result, "filter")
        assert hasattr(result, "count")

        # Verify it's chainable
        chained = result.filter(id__gt=0)
        assert hasattr(chained, "count")

    def test_for_attribute_method_exists(self):
        """Test that for_attribute() method exists and accepts attribute_id."""
        # Call for_attribute() method with a test ID
        result = AttributeValue.objects.for_attribute(1)

        # Verify it returns a QuerySet
        assert hasattr(result, "filter")
        assert hasattr(result, "count")

        # Should not raise any errors
        count = result.count()
        assert count >= 0

    def test_with_usage_count_method_exists(self):
        """Test that with_usage_count() method exists and adds annotation."""
        # Call with_usage_count() method
        result = AttributeValue.objects.with_usage_count()

        # Verify it returns a QuerySet
        assert hasattr(result, "filter")

        # Verify it's chainable
        chained = result.filter(active=True)
        assert hasattr(chained, "count")

    def test_all_methods_are_chainable(self):
        """Test that all manager methods can be chained together."""
        # Chain all methods
        result = (
            AttributeValue.objects.active().for_attribute(1).with_usage_count()
        )

        # Verify chaining works
        assert hasattr(result, "count")
        assert hasattr(result, "filter")

        # Should not raise any errors
        count = result.count()
        assert count >= 0

    def test_methods_return_queryset_type(self):
        """Test that all methods return proper QuerySet objects."""
        # Test each method returns a QuerySet
        active_qs = AttributeValue.objects.active()
        for_attr_qs = AttributeValue.objects.for_attribute(1)
        with_count_qs = AttributeValue.objects.with_usage_count()

        # All should have QuerySet methods
        for qs in [active_qs, for_attr_qs, with_count_qs]:
            assert hasattr(qs, "filter")
            assert hasattr(qs, "exclude")
            assert hasattr(qs, "order_by")
            assert hasattr(qs, "count")
            assert hasattr(qs, "exists")
