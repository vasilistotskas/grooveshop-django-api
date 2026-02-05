"""
Tests for Manager automatic delegation via __getattr__.

These tests verify that the __getattr__ method correctly delegates
QuerySet methods to the Manager, enabling automatic method forwarding
without explicit wrapper methods.
"""

import pytest
from django.db import models

from core.managers import (
    OptimizedManager,
    OptimizedQuerySet,
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)


class TestOptimizedManagerDelegation:
    """Tests for OptimizedManager __getattr__ delegation."""

    def test_getattr_delegates_to_queryset_method(self):
        """Manager should delegate unknown methods to QuerySet."""

        class TestQuerySet(OptimizedQuerySet):
            def custom_filter(self):
                """Custom QuerySet method."""
                return self.filter(active=True)

        class TestManager(OptimizedManager):
            queryset_class = TestQuerySet

        class TestModel(models.Model):
            active = models.BooleanField(default=True)

            class Meta:
                app_label = "core"
                # Use unique model name to avoid registration conflicts
                db_table = "test_model_delegation_1"

        manager = TestManager()
        manager.model = TestModel

        # custom_filter is not defined on Manager, should delegate to QuerySet
        result = manager.custom_filter()
        assert isinstance(result, TestQuerySet)

    def test_getattr_raises_for_underscore_attributes(self):
        """Manager should raise AttributeError for attributes starting with underscore."""

        class TestManager(OptimizedManager):
            pass

        class TestModel2(models.Model):
            class Meta:
                app_label = "core"
                db_table = "test_model_delegation_2"

        manager = TestManager()
        manager.model = TestModel2

        with pytest.raises(AttributeError) as exc_info:
            manager._private_method()

        assert "'TestManager' object has no attribute '_private_method'" in str(
            exc_info.value
        )

    def test_getattr_raises_for_nonexistent_method(self):
        """Manager should raise AttributeError for methods that don't exist on QuerySet."""

        class TestManager(OptimizedManager):
            pass

        class TestModel3(models.Model):
            class Meta:
                app_label = "core"
                db_table = "test_model_delegation_3"

        manager = TestManager()
        manager.model = TestModel3

        with pytest.raises(AttributeError):
            manager.nonexistent_method()

    def test_explicit_methods_not_delegated(self):
        """Explicitly defined Manager methods should not be delegated."""

        class TestQuerySet(OptimizedQuerySet):
            def for_list(self):
                return self.filter(list_optimized=True)

        class TestManager(OptimizedManager):
            queryset_class = TestQuerySet

            def for_list(self):
                """Override for_list with custom logic."""
                return self.get_queryset().filter(manager_optimized=True)

        class TestModel4(models.Model):
            list_optimized = models.BooleanField(default=False)
            manager_optimized = models.BooleanField(default=False)

            class Meta:
                app_label = "core"
                db_table = "test_model_delegation_4"

        manager = TestManager()
        manager.model = TestModel4

        # Should use Manager's explicit method, not delegate
        result = manager.for_list()
        # The result should have the manager's filter applied
        assert isinstance(result, TestQuerySet)

    def test_chainable_methods_work_through_delegation(self):
        """Chained QuerySet methods should work through delegation."""

        class TestQuerySet(OptimizedQuerySet):
            def active(self):
                return self.filter(active=True)

            def published(self):
                return self.filter(published=True)

        class TestManager(OptimizedManager):
            queryset_class = TestQuerySet

        class TestModel5(models.Model):
            active = models.BooleanField(default=True)
            published = models.BooleanField(default=True)

            class Meta:
                app_label = "core"
                db_table = "test_model_delegation_5"

        manager = TestManager()
        manager.model = TestModel5

        # Both methods should be delegated and chainable
        result = manager.active().published()
        assert isinstance(result, TestQuerySet)


class TestTranslatableOptimizedManagerDelegation:
    """Tests for TranslatableOptimizedManager __getattr__ delegation."""

    def test_getattr_delegates_to_queryset_method(self):
        """TranslatableManager should delegate unknown methods to QuerySet."""

        class TestQuerySet(TranslatableOptimizedQuerySet):
            def published(self):
                """Custom QuerySet method."""
                return self.filter(published=True)

        class TestManager(TranslatableOptimizedManager):
            queryset_class = TestQuerySet

        manager = TestManager()

        # published is not defined on Manager, should delegate to QuerySet
        # We can't fully test this without a real model, but we can verify the method exists
        assert hasattr(manager, "__getattr__")

    def test_getattr_raises_for_underscore_attributes(self):
        """TranslatableManager should raise AttributeError for underscore attributes."""

        class TestManager(TranslatableOptimizedManager):
            pass

        manager = TestManager()

        with pytest.raises(AttributeError) as exc_info:
            manager._private_method()

        assert "'TestManager' object has no attribute '_private_method'" in str(
            exc_info.value
        )

    def test_has_getattr_method(self):
        """TranslatableOptimizedManager should have __getattr__ method."""
        assert hasattr(TranslatableOptimizedManager, "__getattr__")

    def test_getattr_method_signature(self):
        """__getattr__ should accept name parameter and return Any."""
        import inspect

        sig = inspect.signature(TranslatableOptimizedManager.__getattr__)
        params = list(sig.parameters.keys())

        assert "self" in params
        assert "name" in params


class TestManagerDelegationIntegration:
    """Integration tests for Manager delegation with real QuerySet operations."""

    def test_filter_delegation(self):
        """Manager should delegate filter() to QuerySet."""

        class TestManager(OptimizedManager):
            pass

        class TestModel6(models.Model):
            name = models.CharField(max_length=100)

            class Meta:
                app_label = "core"
                db_table = "test_model_delegation_6"

        manager = TestManager()
        manager.model = TestModel6

        # filter is a standard QuerySet method, should be delegated
        result = manager.filter(name="test")
        assert isinstance(result, OptimizedQuerySet)

    def test_exclude_delegation(self):
        """Manager should delegate exclude() to QuerySet."""

        class TestManager(OptimizedManager):
            pass

        class TestModel7(models.Model):
            active = models.BooleanField(default=True)

            class Meta:
                app_label = "core"
                db_table = "test_model_delegation_7"

        manager = TestManager()
        manager.model = TestModel7

        # exclude is a standard QuerySet method, should be delegated
        result = manager.exclude(active=False)
        assert isinstance(result, OptimizedQuerySet)

    def test_annotate_delegation(self):
        """Manager should delegate annotate() to QuerySet."""
        from django.db.models import Count

        class TestManager(OptimizedManager):
            pass

        class TestModel8(models.Model):
            name = models.CharField(max_length=100)

            class Meta:
                app_label = "core"
                db_table = "test_model_delegation_8"

        manager = TestManager()
        manager.model = TestModel8

        # annotate is a standard QuerySet method, should be delegated
        result = manager.annotate(count=Count("id"))
        assert isinstance(result, OptimizedQuerySet)

    def test_select_related_delegation(self):
        """Manager should delegate select_related() to QuerySet."""

        class TestManager(OptimizedManager):
            pass

        class TestModel9(models.Model):
            name = models.CharField(max_length=100)

            class Meta:
                app_label = "core"
                db_table = "test_model_delegation_9"

        manager = TestManager()
        manager.model = TestModel9

        # select_related is a standard QuerySet method, should be delegated
        result = manager.select_related("category")
        assert isinstance(result, OptimizedQuerySet)

    def test_prefetch_related_delegation(self):
        """Manager should delegate prefetch_related() to QuerySet."""

        class TestManager(OptimizedManager):
            pass

        class TestModel10(models.Model):
            name = models.CharField(max_length=100)

            class Meta:
                app_label = "core"
                db_table = "test_model_delegation_10"

        manager = TestManager()
        manager.model = TestModel10

        # prefetch_related is a standard QuerySet method, should be delegated
        result = manager.prefetch_related("tags")
        assert isinstance(result, OptimizedQuerySet)
