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

    def test_exclude_deleted_and_deleted_only_partition_the_rows(self):
        """exclude_deleted() and deleted_only() split every row between them."""
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
        all_products = set(Product._base_manager.values_list("id", flat=True))

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
        # Create products - for_list() filters by active=True
        active_product = ProductFactory(is_deleted=False, active=True)
        deleted_product = ProductFactory(
            is_deleted=True, deleted_at=timezone.now(), active=True
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

        assert Product._base_manager.count() == 15


@pytest.mark.django_db
class TestBulkDeleteIsSoftForEverySoftDeleteModel:
    """``Product.delete()`` marks the row; a bulk delete of products (the
    admin's "Delete selected") removed it for good, while orders and
    builder documents soft-delete in both forms. One queryset now does it
    for all three."""

    def test_a_bulk_product_delete_keeps_the_row_flagged(self):
        product = ProductFactory(num_images=0, num_reviews=0)

        result = Product.objects.filter(pk=product.pk).delete()

        row = Product._base_manager.get(pk=product.pk)
        assert row.is_deleted is True
        assert row.deleted_at is not None
        assert result == (1, {"product.Product": 1})

    def test_restore_and_hard_delete(self):
        product = ProductFactory(num_images=0, num_reviews=0)
        Product.objects.filter(pk=product.pk).delete()

        assert Product.objects.deleted_only().filter(pk=product.pk).restore()
        assert not Product._base_manager.get(pk=product.pk).is_deleted

        Product.objects.filter(pk=product.pk).hard_delete()
        assert not Product._base_manager.filter(pk=product.pk).exists()

    def test_a_bulk_delete_sends_each_rows_post_save(self):
        """The receivers a single delete runs (cache purge, search
        reindex, IndexNow) run for a bulk delete too: one UPDATE would
        have sent no signal and left the search index serving them."""
        from django.db.models.signals import post_save

        products = [
            ProductFactory(num_images=0, num_reviews=0) for _ in range(2)
        ]
        saved = []

        def spy(sender, instance, update_fields=None, **kwargs):
            saved.append((instance.pk, frozenset(update_fields or ())))

        post_save.connect(spy, sender=Product, weak=False)
        try:
            Product.objects.filter(pk__in=[p.pk for p in products]).delete()
        finally:
            post_save.disconnect(spy, sender=Product)

        assert sorted(saved) == sorted(
            (p.pk, frozenset({"deleted_at", "is_deleted"})) for p in products
        )

    def test_a_row_already_deleted_keeps_its_deleted_at(self):
        product = ProductFactory(num_images=0, num_reviews=0)
        product.delete()
        first = Product._base_manager.get(pk=product.pk).deleted_at

        result = Product.objects.filter(pk=product.pk).delete()

        assert result == (0, {"product.Product": 0})
        assert Product._base_manager.get(pk=product.pk).deleted_at == first

    def test_a_values_queryset_refuses_delete_and_restore(self):
        """As Django's own ``delete()`` does: the rows are dicts."""
        ProductFactory(num_images=0, num_reviews=0)

        with pytest.raises(TypeError):
            Product.objects.values("pk").delete()
        with pytest.raises(TypeError):
            Product.objects.values_list("pk").restore()
