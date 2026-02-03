from __future__ import annotations

from typing import TYPE_CHECKING

from core.managers import OptimizedManager, OptimizedQuerySet

if TYPE_CHECKING:
    from typing import Self


class ContactQuerySet(OptimizedQuerySet):
    """
    Optimized QuerySet for Contact model.

    Provides chainable methods for common operations and
    standardized `for_list()` and `for_detail()` methods.
    """

    def for_list(self) -> Self:
        """
        Optimized queryset for list views.

        Simple model with no relations to prefetch.
        """
        return self

    def for_detail(self) -> Self:
        """
        Optimized queryset for detail views.

        Same as for_list() for this simple model.
        """
        return self.for_list()

    def by_email_domain(self, domain):
        return self.filter(email__iendswith=f"@{domain}")


class ContactManager(OptimizedManager):
    """
    Manager for Contact model with optimized queryset methods.

    Most methods are automatically delegated to ContactQuerySet
    via __getattr__. Only for_list() and for_detail() are explicitly
    defined for IDE support.

    Usage in ViewSet:
        def get_queryset(self):
            if self.action == "list":
                return Contact.objects.for_list()
            return Contact.objects.for_detail()
    """

    queryset_class = ContactQuerySet

    def get_queryset(self) -> ContactQuerySet:
        return ContactQuerySet(self.model, using=self._db)

    def for_list(self) -> ContactQuerySet:
        """Return optimized queryset for list views."""
        return self.get_queryset().for_list()

    def for_detail(self) -> ContactQuerySet:
        """Return optimized queryset for detail views."""
        return self.get_queryset().for_detail()
