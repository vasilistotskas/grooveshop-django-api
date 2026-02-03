from __future__ import annotations

from typing import TYPE_CHECKING

from django.contrib.contenttypes.models import ContentType
from django.db import models
from parler.managers import TranslatableManager, TranslatableQuerySet

if TYPE_CHECKING:
    from typing import Self


class TagQuerySet(TranslatableQuerySet):
    """
    Optimized QuerySet for Tag model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_translations(self) -> Self:
        """Prefetch translations for better performance."""
        return self.prefetch_related("translations")

    def active(self) -> Self:
        return self.filter(active=True)

    def inactive(self) -> Self:
        return self.filter(active=False)

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes translations.
        """
        return self.with_translations()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Same as for_list() for tags.
        """
        return self.for_list()


class TagManager(TranslatableManager):
    """
    Manager for Tag model with optimized queryset methods.

    Most methods are automatically delegated to TagQuerySet
    via __getattr__. Only for_list() and for_detail() are explicitly
    defined for IDE support.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return Tag.objects.for_list()
            return Tag.objects.for_detail()
    """

    queryset_class = TagQuerySet

    def get_queryset(self) -> TagQuerySet:
        return TagQuerySet(self.model, using=self._db)

    def __getattr__(self, name: str):
        """Delegate unknown attributes to the queryset."""
        if name.startswith("_"):
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )
        return getattr(self.get_queryset(), name)

    def for_list(self) -> TagQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> TagQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()


class TaggedItemQuerySet(models.QuerySet):
    """
    Optimized QuerySet for TaggedItem model.
    """

    def with_tag(self) -> Self:
        """Select related tag with translations."""
        return self.select_related("tag", "content_type").prefetch_related(
            "tag__translations"
        )

    def active_tags(self) -> Self:
        return self.filter(tag__active=True)

    def for_list(self) -> Self:
        """Optimized queryset for list views."""
        return self.with_tag()

    def for_detail(self) -> Self:
        """Optimized queryset for detail views."""
        return self.for_list()


class TaggedItemManager(models.Manager):
    """
    Manager for TaggedItem model with optimized queryset methods.

    Most methods are automatically delegated to TaggedItemQuerySet
    via __getattr__. Only for_list() and for_detail() are explicitly
    defined for IDE support.
    """

    queryset_class = TaggedItemQuerySet

    def get_queryset(self) -> TaggedItemQuerySet:
        return TaggedItemQuerySet(self.model, using=self._db)

    def __getattr__(self, name: str):
        """Delegate unknown attributes to the queryset."""
        if name.startswith("_"):
            raise AttributeError(
                f"'{type(self).__name__}' object has no attribute '{name}'"
            )
        return getattr(self.get_queryset(), name)

    def for_list(self) -> TaggedItemQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> TaggedItemQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()

    def get_tags_for(self, obj_type, obj_id) -> TaggedItemQuerySet:
        """Get tags for a specific object."""
        content_type = ContentType.objects.get_for_model(obj_type)
        return self.for_list().filter(
            content_type=content_type, object_id=obj_id
        )
