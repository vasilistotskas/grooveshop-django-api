from __future__ import annotations

from typing import TYPE_CHECKING

from core.managers import (
    TranslatableOptimizedManager,
    TranslatableOptimizedQuerySet,
)

if TYPE_CHECKING:
    from typing import Self


class BlogTagQuerySet(TranslatableOptimizedQuerySet):
    """
    Optimized QuerySet for BlogTag model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def with_translations(self) -> Self:
        """Prefetch translations for better performance."""
        return self.prefetch_related("translations")

    def active_only(self) -> Self:
        """Filter to active tags only."""
        return self.filter(active=True)

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Includes translations, and ACTIVE tags only — the public read
        paths are the ones that should hide a deactivated tag. The
        filter used to live in ``BlogTagManager.get_queryset``, which
        made it the DEFAULT manager's behaviour and therefore
        ``_default_manager``'s: an inactive tag vanished from the admin
        changelist too, and since ``BlogTagAdmin.list_editable``
        contains ``active``, a merchant who unticked it could never tick
        it back without database access. Verified: after flipping
        ``active`` to False, ``BlogTag.objects.filter(pk=...).exists()``
        was False.

        It also silently narrowed ``get_ordering_queryset()``, so
        ``SortableModel.move_up``/``move_down`` skipped inactive
        neighbours and produced gaps.
        """
        return self.active_only().with_translations()

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Same as for_list() for this simple model.
        """
        return self.for_list()


class BlogTagManager(TranslatableOptimizedManager):
    """
    Manager for BlogTag model with optimized queryset methods.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return BlogTag.objects.for_list()
            return BlogTag.objects.for_detail()
    """

    queryset_class = BlogTagQuerySet

    def get_queryset(self) -> BlogTagQuerySet:
        # No ``active_only()`` here — see ``for_list``. A default manager
        # that hides rows hides them from the admin as well.
        return BlogTagQuerySet(self.model, using=self._db)
