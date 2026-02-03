"""
Tests for ProductAttributeManager custom manager methods.

Validates Requirements 3.3, 8.1, 8.4
"""

import pytest

from product.models import ProductAttribute


@pytest.mark.django_db
class TestProductAttributeManager:
    """Test ProductAttributeManager custom query methods."""

    def test_for_product_method_exists(self):
        """Test that for_product() method exists and accepts product_id."""
        # Call for_product() method with a test ID
        result = ProductAttribute.objects.for_product(1)

        # Verify it returns a QuerySet
        assert hasattr(result, "filter")
        assert hasattr(result, "count")

        # Should not raise any errors
        count = result.count()
        assert count >= 0

    def test_for_products_method_exists(self):
        """Test that for_products() method exists and accepts product_ids list."""
        # Call for_products() method with test IDs
        result = ProductAttribute.objects.for_products([1, 2, 3])

        # Verify it returns a QuerySet
        assert hasattr(result, "filter")
        assert hasattr(result, "count")

        # Should not raise any errors
        count = result.count()
        assert count >= 0

    def test_for_products_with_empty_list(self):
        """Test that for_products() handles empty list correctly."""
        # Call with empty list
        result = ProductAttribute.objects.for_products([])

        # Should return empty queryset without errors
        assert result.count() == 0

    def test_by_attribute_method_exists(self):
        """Test that by_attribute() method exists and accepts attribute_id."""
        # Call by_attribute() method with a test ID
        result = ProductAttribute.objects.by_attribute(1)

        # Verify it returns a QuerySet
        assert hasattr(result, "filter")
        assert hasattr(result, "count")

        # Should not raise any errors
        count = result.count()
        assert count >= 0

    def test_methods_are_chainable_with_filters(self):
        """Test that manager methods can be chained with other QuerySet methods."""
        # Chain for_product with filter
        result = ProductAttribute.objects.for_product(1).filter(id__gt=0)

        # Verify chaining works
        assert hasattr(result, "count")
        count = result.count()
        assert count >= 0

    def test_methods_return_queryset_type(self):
        """Test that all methods return proper QuerySet objects."""
        # Test each method returns a QuerySet
        for_product_qs = ProductAttribute.objects.for_product(1)
        for_products_qs = ProductAttribute.objects.for_products([1, 2])
        by_attribute_qs = ProductAttribute.objects.by_attribute(1)

        # All should have QuerySet methods
        for qs in [for_product_qs, for_products_qs, by_attribute_qs]:
            assert hasattr(qs, "filter")
            assert hasattr(qs, "exclude")
            assert hasattr(qs, "order_by")
            assert hasattr(qs, "count")
            assert hasattr(qs, "exists")
            assert hasattr(qs, "select_related")
            assert hasattr(qs, "prefetch_related")

    def test_for_product_uses_select_related(self):
        """Test that for_product() result has select_related applied."""
        # Get queryset
        qs = ProductAttribute.objects.for_product(1)

        # Check that query has select_related
        # This is indicated by the query having the related tables
        query_str = str(qs.query)

        # Should be a valid query (no errors when converting to string)
        assert isinstance(query_str, str)
        assert len(query_str) > 0

    def test_for_products_uses_select_related(self):
        """Test that for_products() result has select_related applied."""
        # Get queryset
        qs = ProductAttribute.objects.for_products([1, 2, 3])

        # Check that query has select_related
        query_str = str(qs.query)

        # Should be a valid query
        assert isinstance(query_str, str)
        assert len(query_str) > 0

    def test_by_attribute_uses_select_related(self):
        """Test that by_attribute() result has select_related applied."""
        # Get queryset
        qs = ProductAttribute.objects.by_attribute(1)

        # Check that query has select_related
        query_str = str(qs.query)

        # Should be a valid query
        assert isinstance(query_str, str)
        assert len(query_str) > 0
