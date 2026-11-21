"""Soft delete for querysets and managers: the one implementation.

A soft-deleted row keeps its data and is marked ``is_deleted`` with its
``deleted_at`` (``core.models.SoftDeleteModel``). ``SoftDeleteModel.delete()``
marks one row; :class:`SoftDeleteQuerySetMixin` gives a bulk delete the
same meaning, row by row so the same signals run, and
``Model.objects.filter(...).delete()`` and the admin's "Delete selected"
never remove a row for good: ``hard_delete()`` does.

Compose the mixins into an app's own classes, before the Django base in
the inheritance list so their methods win::

    class OrderQuerySet(SoftDeleteQuerySetMixin, OptimizedQuerySet): ...

    class OrderManager(SoftDeleteManagerMixin, OptimizedManager):
        queryset_class = OrderQuerySet

A model with no queryset of its own uses ``core.models.SoftDeleteManager``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.db import transaction

if TYPE_CHECKING:
    from typing import Self


class SoftDeleteQuerySetMixin:
    """Filters on the deletion state, and soft deletes in bulk.

    The model has ``is_deleted`` and ``deleted_at`` fields.
    """

    def exclude_deleted(self) -> Self:
        """The rows that are not soft-deleted."""
        return self.exclude(is_deleted=True)

    def deleted_only(self) -> Self:
        """The soft-deleted rows."""
        return self.filter(is_deleted=True)

    def delete(self) -> tuple[int, dict[str, int]]:
        """Soft-delete every row through its own ``delete()``.

        Row by row, in pk order and in one transaction, so a bulk delete
        (the admin's "Delete selected") sends the ``post_save`` signals the
        single-row delete sends: the storefront cache purge, the
        Meilisearch reindex, IndexNow and the history row. One UPDATE would
        have sent none. A row already deleted keeps its ``deleted_at``.
        Returns Django's ``(count, {model label: count})``.
        """
        count = 0
        with transaction.atomic():
            for row in self.exclude_deleted().order_by("pk"):
                row.delete()
                count += 1
        return count, {self.model._meta.label: count}

    def restore(self) -> int:
        """Un-delete every soft-deleted row through its own ``restore()``,
        for the same signals; returns how many."""
        count = 0
        with transaction.atomic():
            for row in self.deleted_only().order_by("pk"):
                row.restore()
                count += 1
        return count

    def hard_delete(self) -> tuple[int, dict[str, int]]:
        """Remove the rows for good (Django's own ``delete()``)."""
        return super().delete()


class SoftDeleteManagerMixin:
    """A manager that hides soft-deleted rows, with the two ways back.

    Its queryset class composes :class:`SoftDeleteQuerySetMixin`.
    """

    def get_queryset(self) -> Any:
        """The rows that are not soft-deleted."""
        return super().get_queryset().exclude_deleted()

    def all_with_deleted(self) -> Any:
        """Every row, soft-deleted ones included (admin, audit, purge)."""
        return super().get_queryset()

    def deleted_only(self) -> Any:
        """Only the soft-deleted rows."""
        return super().get_queryset().deleted_only()
