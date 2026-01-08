"""
Core mixins for reusable QuerySet optimization patterns.

This module provides mixins that can be composed into custom QuerySets
to handle common patterns like translations, count annotations, and related data.

Usage:
    from core.mixins import TranslationsMixin, CountAnnotationsMixin, RelatedDataMixin

    class ProductQuerySet(TranslationsMixin, CountAnnotationsMixin, models.QuerySet):
        def for_list(self):
            return (
                self.with_translations()
                    .with_likes_count(field_name='favourites')
                    .with_reviews_count()
            )

Note:
    Mixins should be listed before models.QuerySet in the inheritance chain
    to ensure proper method resolution order (MRO).

Available Mixins:
    - TranslationsMixin: For Parler translatable models
    - CountAnnotationsMixin: For count/aggregate annotations
    - RelatedDataMixin: For common select_related/prefetch_related patterns
"""

from core.mixins.queryset import (
    CountAnnotationsMixin,
    RelatedDataMixin,
    TranslationsMixin,
)

__all__ = [
    "TranslationsMixin",
    "CountAnnotationsMixin",
    "RelatedDataMixin",
]
