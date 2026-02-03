"""
Integration tests for SoftDeleteQuerySetMixin.

These tests verify that the SoftDeleteQuerySetMixin works correctly
with actual database models that use the SoftDeleteModel base class.
"""

import pytest
from django.utils import timezone

from product.factories.product import ProductFactory
from product.models.product import Product


@pytest.mark.django_db
class TestSoftDeleteQuerySetMixin:
    """Integration tests for SoftDeleteQuerySetMixin with Product model."""

    def test_exclude_deleted_filters_out_deleted_records(self):
        """exclude_deleted() should filter out records where is_deleted=True."""
        # Create active and deleted products
        active_product = ProductFactory(is_deleted=False)
        deleted_product = ProductFactory(
            is_deleted=True, deleted_at=timezone.now()
        )

        # Query using exclude_deleted
        results = Product.objects.exclude_deleted()

        # Verify only active product is returned
        assert active_product in results
        assert deleted_product not in results
        assert results.count() == 1

    def test_deleted_only_returns_only_deleted_records(self):
        """deleted_only() should return only records where is_deleted=True."""
        # Create active and deleted products
        active_product = ProductFactory(is_deleted=False)
        deleted_product = ProductFactory(
            is_deleted=True, deleted_at=timezone.now()
        )

        # Query using deleted_only
        results = Product.objects.deleted_only()

        # Verify only deleted product is returned
        assert active_product not in results
        assert deleted_product in results
        assert results.count() == 1

    def test_with_deleted_returns_all_records(self):
        """with_deleted() should return all records regardless of deletion status."""
        # Create active and deleted products
        active_product = ProductFactory(is_deleted=False)
        deleted_product = ProductFactory(
            is_deleted=True, deleted_at=timezone.now()
        )

        # Query using with_deleted
        results = Product.objects.with_deleted()

        # Verify both products are returned
        assert active_product in results
        assert deleted_product in results
        assert results.count() == 2

    def test_union_of_exclude_and_deleted_equals_with_deleted(self):
        """
        The union of exclude_deleted() and deleted_only() should equal with_deleted().

        This validates Property 3 from the design document:
        The union of exclude_deleted() and deleted_only() should equal with_deleted()
        """
        # Create multiple products with mixed deletion status
        ProductFactory.create_batch(3, is_deleted=False)
        ProductFactory.create_batch(
            2, is_deleted=True, deleted_at=timezone.now()
        )

        # Get all three querysets
        active_products = set(
            Product.objects.exclude_deleted().values_list("id", flat=True)
        )
        deleted_products = set(
            Product.objects.deleted_only().values_list("id", flat=True)
        )
        all_products = set(
            Product.objects.with_deleted().values_list("id", flat=True)
        )

        # Verify the union property
        assert active_products | deleted_products == all_products
        assert len(active_products) == 3
        assert len(deleted_products) == 2
        assert len(all_products) == 5

    def test_soft_delete_methods_are_chainable(self):
        """Soft delete methods should be chainable with other QuerySet methods."""
        # Create products
        active_product = ProductFactory(is_deleted=False, active=True)
        inactive_product = ProductFactory(is_deleted=False, active=False)
        deleted_product = ProductFactory(
            is_deleted=True, deleted_at=timezone.now(), active=True
        )

        # Chain exclude_deleted with filter
        results = Product.objects.exclude_deleted().filter(active=True)

        # Verify chaining works correctly
        assert active_product in results
        assert inactive_product not in results
        assert deleted_product not in results
        assert results.count() == 1

    def test_soft_delete_with_for_list_optimization(self):
        """Soft delete methods should work with for_list() optimization."""
        # Create products
        active_product = ProductFactory(is_deleted=False)
        deleted_product = ProductFactory(
            is_deleted=True, deleted_at=timezone.now()
        )

        # Use for_list which should automatically exclude deleted
        results = Product.objects.for_list()

        # Verify for_list excludes deleted products
        assert active_product in results
        assert deleted_product not in results

    def test_multiple_soft_delete_filters(self):
        """Test multiple products with various deletion states."""
        # Create 10 active and 5 deleted products
        active_products = ProductFactory.create_batch(10, is_deleted=False)
        deleted_products = ProductFactory.create_batch(
            5, is_deleted=True, deleted_at=timezone.now()
        )

        # Test exclude_deleted
        active_results = Product.objects.exclude_deleted()
        assert active_results.count() == 10
        for product in active_products:
            assert product in active_results

        # Test deleted_only
        deleted_results = Product.objects.deleted_only()
        assert deleted_results.count() == 5
        for product in deleted_products:
            assert product in deleted_results

        # Test with_deleted
        all_results = Product.objects.with_deleted()
        assert all_results.count() == 15
