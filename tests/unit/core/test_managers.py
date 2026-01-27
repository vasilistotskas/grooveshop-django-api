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
    CountAnnotationsMixin,
    RelatedDataMixin,
    TranslationsMixin,
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


class TestTranslationsMixin:
    """Tests for TranslationsMixin."""

    def test_with_translations_returns_self_for_non_translatable(self):
        """with_translations() should return self if model has no translations."""

        class MockQuerySet(TranslationsMixin):
            def __init__(self):
                self.model = type("Model", (), {})()

            def prefetch_related(self, *args):
                return self

        qs = MockQuerySet()
        result = qs.with_translations()
        assert result is qs

    def test_with_translations_prefetches_for_translatable(self):
        """with_translations() should prefetch translations if model has them."""
        prefetch_called = []

        class MockQuerySet(TranslationsMixin):
            def __init__(self):
                self.model = type("Model", (), {"translations": True})()

            def prefetch_related(self, *args):
                prefetch_called.extend(args)
                return self

        qs = MockQuerySet()
        qs.with_translations()
        assert "translations" in prefetch_called


class TestCountAnnotationsMixin:
    """Tests for CountAnnotationsMixin."""

    def test_with_likes_count_annotates(self):
        """with_likes_count() should annotate with _likes_count."""
        annotate_kwargs = {}

        class MockQuerySet(CountAnnotationsMixin):
            def annotate(self, **kwargs):
                annotate_kwargs.update(kwargs)
                return self

        qs = MockQuerySet()
        qs.with_likes_count()
        assert "_likes_count" in annotate_kwargs

    def test_with_likes_count_custom_field(self):
        """with_likes_count() should use custom field name."""
        annotate_kwargs = {}

        class MockQuerySet(CountAnnotationsMixin):
            def annotate(self, **kwargs):
                annotate_kwargs.update(kwargs)
                return self

        qs = MockQuerySet()
        qs.with_likes_count(field_name="favourites")
        assert "_likes_count" in annotate_kwargs

    def test_with_comments_count_annotates(self):
        """with_comments_count() should annotate with _comments_count."""
        annotate_kwargs = {}

        class MockQuerySet(CountAnnotationsMixin):
            def annotate(self, **kwargs):
                annotate_kwargs.update(kwargs)
                return self

        qs = MockQuerySet()
        qs.with_comments_count()
        assert "_comments_count" in annotate_kwargs

    def test_with_reviews_count_annotates(self):
        """with_reviews_count() should annotate with _reviews_count."""
        annotate_kwargs = {}

        class MockQuerySet(CountAnnotationsMixin):
            def annotate(self, **kwargs):
                annotate_kwargs.update(kwargs)
                return self

        qs = MockQuerySet()
        qs.with_reviews_count()
        assert "_reviews_count" in annotate_kwargs

    def test_with_review_average_annotates(self):
        """with_review_average() should annotate with _review_average."""
        annotate_kwargs = {}

        class MockQuerySet(CountAnnotationsMixin):
            def annotate(self, **kwargs):
                annotate_kwargs.update(kwargs)
                return self

        qs = MockQuerySet()
        qs.with_review_average()
        assert "_review_average" in annotate_kwargs

    def test_with_tags_count_annotates(self):
        """with_tags_count() should annotate with _tags_count."""
        annotate_kwargs = {}

        class MockQuerySet(CountAnnotationsMixin):
            def annotate(self, **kwargs):
                annotate_kwargs.update(kwargs)
                return self

        qs = MockQuerySet()
        qs.with_tags_count()
        assert "_tags_count" in annotate_kwargs

    def test_with_items_count_annotates(self):
        """with_items_count() should annotate with _items_count."""
        annotate_kwargs = {}

        class MockQuerySet(CountAnnotationsMixin):
            def annotate(self, **kwargs):
                annotate_kwargs.update(kwargs)
                return self

        qs = MockQuerySet()
        qs.with_items_count()
        assert "_items_count" in annotate_kwargs

    def test_with_favourites_count_annotates(self):
        """with_favourites_count() should annotate with _favourites_count."""
        annotate_kwargs = {}

        class MockQuerySet(CountAnnotationsMixin):
            def annotate(self, **kwargs):
                annotate_kwargs.update(kwargs)
                return self

        qs = MockQuerySet()
        qs.with_favourites_count()
        assert "_favourites_count" in annotate_kwargs


class TestRelatedDataMixin:
    """Tests for RelatedDataMixin."""

    def test_with_user_selects_related(self):
        """with_user() should select_related user."""
        select_args = []

        class MockQuerySet(RelatedDataMixin):
            def select_related(self, *args):
                select_args.extend(args)
                return self

        qs = MockQuerySet()
        qs.with_user()
        assert "user" in select_args

    def test_with_user_custom_field(self):
        """with_user() should use custom field name."""
        select_args = []

        class MockQuerySet(RelatedDataMixin):
            def select_related(self, *args):
                select_args.extend(args)
                return self

        qs = MockQuerySet()
        qs.with_user(field_name="author")
        assert "author" in select_args

    def test_with_category_selects_related(self):
        """with_category() should select_related category."""
        select_args = []

        class MockQuerySet(RelatedDataMixin):
            def select_related(self, *args):
                select_args.extend(args)
                return self

        qs = MockQuerySet()
        qs.with_category()
        assert "category" in select_args

    def test_with_country_selects_related(self):
        """with_country() should select_related country."""
        select_args = []

        class MockQuerySet(RelatedDataMixin):
            def select_related(self, *args):
                select_args.extend(args)
                return self

        qs = MockQuerySet()
        qs.with_country()
        assert "country" in select_args

    def test_with_region_selects_related(self):
        """with_region() should select_related region."""
        select_args = []

        class MockQuerySet(RelatedDataMixin):
            def select_related(self, *args):
                select_args.extend(args)
                return self

        qs = MockQuerySet()
        qs.with_region()
        assert "region" in select_args

    def test_with_images_prefetches_with_translations(self):
        """with_images() should prefetch images with translations by default."""
        prefetch_args = []

        class MockQuerySet(RelatedDataMixin):
            def prefetch_related(self, *args):
                prefetch_args.extend(args)
                return self

        qs = MockQuerySet()
        qs.with_images()
        assert "images__translations" in prefetch_args

    def test_with_images_without_translations(self):
        """with_images() should prefetch images without translations when specified."""
        prefetch_args = []

        class MockQuerySet(RelatedDataMixin):
            def prefetch_related(self, *args):
                prefetch_args.extend(args)
                return self

        qs = MockQuerySet()
        qs.with_images(include_translations=False)
        assert "images" in prefetch_args
        assert "images__translations" not in prefetch_args

    def test_with_tags_prefetches_with_translations(self):
        """with_tags() should prefetch tags with translations by default."""
        prefetch_args = []

        class MockQuerySet(RelatedDataMixin):
            def prefetch_related(self, *args):
                prefetch_args.extend(args)
                return self

        qs = MockQuerySet()
        qs.with_tags()
        assert "tags__translations" in prefetch_args

    def test_with_tags_without_translations(self):
        """with_tags() should prefetch tags without translations when specified."""
        prefetch_args = []

        class MockQuerySet(RelatedDataMixin):
            def prefetch_related(self, *args):
                prefetch_args.extend(args)
                return self

        qs = MockQuerySet()
        qs.with_tags(include_translations=False)
        assert "tags" in prefetch_args
        assert "tags__translations" not in prefetch_args


class TestMixinComposition:
    """Tests for composing multiple mixins together."""

    def test_can_compose_all_mixins(self):
        """All mixins should be composable into a single QuerySet class."""

        class ComposedQuerySet(
            TranslationsMixin, CountAnnotationsMixin, RelatedDataMixin
        ):
            def __init__(self):
                self.model = type("Model", (), {"translations": True})()
                self._select_related = []
                self._prefetch_related = []
                self._annotate = {}

            def select_related(self, *args):
                self._select_related.extend(args)
                return self

            def prefetch_related(self, *args):
                self._prefetch_related.extend(args)
                return self

            def annotate(self, **kwargs):
                self._annotate.update(kwargs)
                return self

        qs = ComposedQuerySet()

        # Chain all methods
        result = (
            qs.with_translations()
            .with_user()
            .with_category()
            .with_likes_count()
            .with_reviews_count()
            .with_images()
        )

        assert result is qs
        assert "translations" in qs._prefetch_related
        assert "user" in qs._select_related
        assert "category" in qs._select_related
        assert "_likes_count" in qs._annotate
        assert "_reviews_count" in qs._annotate
        assert "images__translations" in qs._prefetch_related
