"""
Tests for core manager and queryset base classes.

These tests verify that the OptimizedQuerySet, OptimizedManager, and related
mixin classes work correctly and provide the expected optimization patterns.
"""

from django.db import models

from core.managers import (
    OptimizedManager,
    OptimizedQuerySet,
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)
from core.mixins import (
    SoftDeleteQuerySetMixin,
)


class TestOptimizedQuerySet:
    """Tests for OptimizedQuerySet base class."""

    def test_with_translations_returns_self_for_non_translatable_model(self):
        """with_translations() should return self if model has no translations."""

        class NonTranslatableModel(models.Model):
            name = models.CharField(max_length=100)

            class Meta:
                app_label = "core"

        qs = OptimizedQuerySet(model=NonTranslatableModel)
        result = qs.with_translations()
        assert result is qs

    def test_for_list_calls_with_translations(self):
        """for_list() should call with_translations() by default."""

        class ListTestModel(models.Model):
            name = models.CharField(max_length=100)

            class Meta:
                app_label = "core"

        qs = OptimizedQuerySet(model=ListTestModel)
        # for_list() should return a queryset (same instance for non-translatable)
        result = qs.for_list()
        assert result is qs

    def test_for_detail_calls_for_list(self):
        """for_detail() should call for_list() by default."""

        class DetailTestModel(models.Model):
            name = models.CharField(max_length=100)

            class Meta:
                app_label = "core"

        qs = OptimizedQuerySet(model=DetailTestModel)
        result = qs.for_detail()
        assert result is qs


class TestOptimizedManager:
    """Tests for OptimizedManager base class."""

    def test_queryset_class_default(self):
        """Default queryset_class should be OptimizedQuerySet."""
        assert OptimizedManager.queryset_class is OptimizedQuerySet

    def test_custom_queryset_class(self):
        """Manager should use custom queryset_class when set."""

        class CustomQuerySet(OptimizedQuerySet):
            pass

        class CustomManager(OptimizedManager):
            queryset_class = CustomQuerySet

        assert CustomManager.queryset_class is CustomQuerySet


class TestTranslatableOptimizedQuerySet:
    """Tests for TranslatableOptimizedQuerySet class."""

    def test_inherits_from_translatable_queryset(self):
        """Should inherit from Parler's TranslatableQuerySet."""
        from parler.managers import TranslatableQuerySet

        assert issubclass(TranslatableOptimizedQuerySet, TranslatableQuerySet)

    def test_has_for_list_method(self):
        """Should have for_list() method."""
        assert hasattr(TranslatableOptimizedQuerySet, "for_list")

    def test_has_for_detail_method(self):
        """Should have for_detail() method."""
        assert hasattr(TranslatableOptimizedQuerySet, "for_detail")

    def test_has_with_translations_method(self):
        """Should have with_translations() method."""
        assert hasattr(TranslatableOptimizedQuerySet, "with_translations")


class TestTranslatableOptimizedManager:
    """Tests for TranslatableOptimizedManager class."""

    def test_inherits_from_translatable_manager(self):
        """Should inherit from Parler's TranslatableManager."""
        from parler.managers import TranslatableManager

        assert issubclass(TranslatableOptimizedManager, TranslatableManager)

    def test_queryset_class_default(self):
        """Default queryset_class should be TranslatableOptimizedQuerySet."""
        assert (
            TranslatableOptimizedManager.queryset_class
            is TranslatableOptimizedQuerySet
        )

    def test_has_for_list_method(self):
        """Should have for_list() method."""
        assert hasattr(TranslatableOptimizedManager, "for_list")

    def test_has_for_detail_method(self):
        """Should have for_detail() method."""
        assert hasattr(TranslatableOptimizedManager, "for_detail")


class TestSoftDeleteQuerySetMixin:
    """Tests for SoftDeleteQuerySetMixin."""

    def test_exclude_deleted_filters_correctly(self):
        """exclude_deleted() should exclude records where is_deleted=True."""
        exclude_kwargs = {}

        class MockQuerySet(SoftDeleteQuerySetMixin):
            def exclude(self, **kwargs):
                exclude_kwargs.update(kwargs)
                return self

        qs = MockQuerySet()
        result = qs.exclude_deleted()
        assert result is qs
        assert exclude_kwargs == {"is_deleted": True}

    def test_with_deleted_returns_all(self):
        """with_deleted() should return all records."""
        all_called = []

        class MockQuerySet(SoftDeleteQuerySetMixin):
            def all(self):
                all_called.append(True)
                return self

        qs = MockQuerySet()
        result = qs.with_deleted()
        assert result is qs
        assert len(all_called) == 1

    def test_deleted_only_filters_correctly(self):
        """deleted_only() should return only records where is_deleted=True."""
        filter_kwargs = {}

        class MockQuerySet(SoftDeleteQuerySetMixin):
            def filter(self, **kwargs):
                filter_kwargs.update(kwargs)
                return self

        qs = MockQuerySet()
        result = qs.deleted_only()
        assert result is qs
        assert filter_kwargs == {"is_deleted": True}

    def test_methods_are_chainable(self):
        """All soft delete methods should be chainable."""
        call_log = []

        class MockQuerySet(SoftDeleteQuerySetMixin):
            def exclude(self, **kwargs):
                call_log.append(("exclude", kwargs))
                return self

            def filter(self, **kwargs):
                call_log.append(("filter", kwargs))
                return self

            def all(self):
                call_log.append(("all", {}))
                return self

        qs = MockQuerySet()

        # Test chaining exclude_deleted
        result = qs.exclude_deleted()
        assert result is qs
        assert ("exclude", {"is_deleted": True}) in call_log

        # Reset and test deleted_only
        call_log.clear()
        result = qs.deleted_only()
        assert result is qs
        assert ("filter", {"is_deleted": True}) in call_log

        # Reset and test with_deleted
        call_log.clear()
        result = qs.with_deleted()
        assert result is qs
        assert ("all", {}) in call_log


class TestMixinComposition:
    """Tests for composing SoftDeleteQuerySetMixin."""

    def test_soft_delete_mixin_is_chainable(self):
        """SoftDeleteQuerySetMixin methods should be chainable."""

        class ComposedQuerySet(SoftDeleteQuerySetMixin):
            def __init__(self):
                self._exclude = {}

            def annotate(self, **kwargs):
                return self

            def exclude(self, **kwargs):
                self._exclude.update(kwargs)
                return self

        qs = ComposedQuerySet()

        # Chain methods
        result = qs.exclude_deleted()

        assert result is qs
        assert qs._exclude == {"is_deleted": True}
