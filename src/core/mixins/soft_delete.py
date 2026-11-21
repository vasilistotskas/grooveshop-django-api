"""Soft delete for querysets and managers: the one implementation.

A soft-deleted row keeps its data and is marked ``is_deleted`` with its
``deleted_at`` (``core.models.SoftDeleteModel``). ``SoftDeleteModel.delete()``
marks one row; :class:`SoftDeleteQuerySetMixin` gives a bulk delete the
same meaning, so ``Model.objects.filter(...).delete()`` and the admin's
"Delete selected" never remove a row for good: ``hard_delete()`` does.

Compose the mixins into an app's own classes, before the Django base in
the inheritance list so their methods win::

    class OrderQuerySet(SoftDeleteQuerySetMixin, OptimizedQuerySet): ...

    class OrderManager(SoftDeleteManagerMixin, OptimizedManager):
        queryset_class = OrderQuerySet

A model with no queryset of its own uses ``core.models.SoftDeleteManager``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.utils import timezone

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
        """Soft-delete every row, as ``SoftDeleteModel.delete()`` does one.

        A single UPDATE: no ``pre_delete``/``post_delete`` or ``post_save``
        signal runs, so a caller that indexes the rows reindexes them
        (``product.signals.reindex_products_by_pk``). Returns Django's
        ``(count, {model label: count})``.
        """
        count = super().update(deleted_at=timezone.now(), is_deleted=True)
        return count, {self.model._meta.label: count}

    def restore(self) -> int:
        """Un-delete the rows; returns how many."""
        return super().update(deleted_at=None, is_deleted=False)

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
