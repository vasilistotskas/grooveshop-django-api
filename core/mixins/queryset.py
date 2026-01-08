"""
Reusable QuerySet mixins for common optimization patterns.

These mixins provide chainable methods that can be composed into custom QuerySets
to handle common patterns like translations, count annotations, and related data.

Usage:
    class ProductQuerySet(TranslationsMixin, CountAnnotationsMixin, models.QuerySet):
        def for_list(self):
            return self.with_translations().with_likes_count()

Note:
    Mixins should be listed before models.QuerySet in the inheritance chain
    to ensure proper method resolution order (MRO).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.db.models import Avg, Count, Q

if TYPE_CHECKING:
    from typing import Self


class TranslationsMixin:
    """
    Mixin for models with Parler translations.

    Provides `with_translations()` method to prefetch translation data,
    avoiding N+1 queries when accessing translated fields.

    Example:
        class ProductQuerySet(TranslationsMixin, models.QuerySet):
            def for_list(self):
                return self.with_translations()
    """

    def with_translations(self) -> Self:
        """
        Prefetch translations for Parler translatable models.

        Returns:
            QuerySet with translations prefetched.
        """
        if hasattr(self.model, "translations"):
            return self.prefetch_related("translations")
        return self


class CountAnnotationsMixin:
    """
    Mixin for common count annotations.

    Provides methods to annotate querysets with counts that can be accessed
    via model properties using the annotation fallback pattern.

    The annotation fallback pattern:
        @property
        def likes_count(self) -> int:
            if hasattr(self, '_likes_count'):
                return self._likes_count
            return self.likes.count()

    Example:
        class ProductQuerySet(CountAnnotationsMixin, models.QuerySet):
            def for_list(self):
                return self.with_likes_count().with_reviews_count()
    """

    def with_likes_count(self, field_name: str = "likes") -> Self:
        """
        Annotate queryset with likes count.

        Args:
            field_name: The related field name for likes (default: 'likes').

        Returns:
            QuerySet annotated with _likes_count.
        """
        return self.annotate(_likes_count=Count(field_name, distinct=True))

    def with_comments_count(
        self,
        field_name: str = "comments",
        approved_only: bool = True,
    ) -> Self:
        """
        Annotate queryset with comments count.

        Args:
            field_name: The related field name for comments (default: 'comments').
            approved_only: If True, only count approved comments (default: True).

        Returns:
            QuerySet annotated with _comments_count.
        """
        if approved_only:
            return self.annotate(
                _comments_count=Count(
                    field_name,
                    distinct=True,
                    filter=Q(**{f"{field_name}__is_approved": True}),
                )
            )
        return self.annotate(_comments_count=Count(field_name, distinct=True))

    def with_reviews_count(self, field_name: str = "reviews") -> Self:
        """
        Annotate queryset with reviews count.

        Args:
            field_name: The related field name for reviews (default: 'reviews').

        Returns:
            QuerySet annotated with _reviews_count.
        """
        return self.annotate(_reviews_count=Count(field_name, distinct=True))

    def with_review_average(
        self, field_name: str = "reviews", rate_field: str = "rate"
    ) -> Self:
        """
        Annotate queryset with average review rating.

        Args:
            field_name: The related field name for reviews (default: 'reviews').
            rate_field: The field name for the rating value (default: 'rate').

        Returns:
            QuerySet annotated with _review_average.
        """
        return self.annotate(_review_average=Avg(f"{field_name}__{rate_field}"))

    def with_tags_count(self, field_name: str = "tags") -> Self:
        """
        Annotate queryset with tags count.

        Args:
            field_name: The related field name for tags (default: 'tags').

        Returns:
            QuerySet annotated with _tags_count.
        """
        return self.annotate(_tags_count=Count(field_name, distinct=True))

    def with_items_count(self, field_name: str = "items") -> Self:
        """
        Annotate queryset with items count.

        Args:
            field_name: The related field name for items (default: 'items').

        Returns:
            QuerySet annotated with _items_count.
        """
        return self.annotate(_items_count=Count(field_name, distinct=True))

    def with_favourites_count(self, field_name: str = "favourites") -> Self:
        """
        Annotate queryset with favourites count.

        Args:
            field_name: The related field name for favourites (default: 'favourites').

        Returns:
            QuerySet annotated with _favourites_count.
        """
        return self.annotate(_favourites_count=Count(field_name, distinct=True))


class RelatedDataMixin:
    """
    Mixin for common related data prefetching patterns.

    Provides methods to select_related and prefetch_related common patterns
    like user, category, images, etc.

    Example:
        class ProductQuerySet(RelatedDataMixin, models.QuerySet):
            def for_list(self):
                return self.with_user().with_category()
    """

    def with_user(self, field_name: str = "user") -> Self:
        """
        Select related user data.

        Args:
            field_name: The field name for the user FK (default: 'user').

        Returns:
            QuerySet with user selected.
        """
        return self.select_related(field_name)

    def with_category(self, field_name: str = "category") -> Self:
        """
        Select related category data.

        Args:
            field_name: The field name for the category FK (default: 'category').

        Returns:
            QuerySet with category selected.
        """
        return self.select_related(field_name)

    def with_country(self, field_name: str = "country") -> Self:
        """
        Select related country data.

        Args:
            field_name: The field name for the country FK (default: 'country').

        Returns:
            QuerySet with country selected.
        """
        return self.select_related(field_name)

    def with_region(self, field_name: str = "region") -> Self:
        """
        Select related region data.

        Args:
            field_name: The field name for the region FK (default: 'region').

        Returns:
            QuerySet with region selected.
        """
        return self.select_related(field_name)

    def with_images(
        self, field_name: str = "images", include_translations: bool = True
    ) -> Self:
        """
        Prefetch related images.

        Args:
            field_name: The field name for images (default: 'images').
            include_translations: If True, also prefetch image translations.

        Returns:
            QuerySet with images prefetched.
        """
        if include_translations:
            return self.prefetch_related(f"{field_name}__translations")
        return self.prefetch_related(field_name)

    def with_tags(
        self, field_name: str = "tags", include_translations: bool = True
    ) -> Self:
        """
        Prefetch related tags.

        Args:
            field_name: The field name for tags (default: 'tags').
            include_translations: If True, also prefetch tag translations.

        Returns:
            QuerySet with tags prefetched.
        """
        if include_translations:
            return self.prefetch_related(f"{field_name}__translations")
        return self.prefetch_related(field_name)
