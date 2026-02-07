from __future__ import annotations

import uuid
from typing import Any

from django.contrib.postgres.indexes import BTreeIndex, GinIndex
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models, transaction
from django.db.models import F, JSONField, Max, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta


class SeoModel(models.Model):
    """
    Abstract model that adds SEO fields (title, description, keywords).
    """

    seo_title = models.CharField(
        _("Seo Title"), max_length=70, blank=True, default=""
    )
    seo_description = models.TextField(
        _("Seo Description"), max_length=300, blank=True, default=""
    )
    seo_keywords = models.CharField(
        _("Seo Keywords"), max_length=255, blank=True, default=""
    )

    class Meta:
        abstract = True


class SortableModel(models.Model):
    """
    Abstract model that adds a sort_order field and methods for moving items up/down.

    Provides thread-safe ordering with database-level locking to prevent race conditions.
    """

    sort_order = models.IntegerField(_("Sort Order"), editable=False, null=True)

    class Meta(TypedModelMeta):
        abstract = True
        indexes = [
            BTreeIndex(fields=["sort_order"], name="%(class)s_sort_order_ix"),
        ]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """
        Save the model instance, automatically assigning sort_order for new instances.

        Uses database-level locking to prevent race conditions when multiple
        instances are created simultaneously.
        """
        if self.pk is None:
            with transaction.atomic():
                # Lock the table to prevent race conditions
                qs = self.get_ordering_queryset().select_for_update()
                existing_max = self.get_max_sort_order(qs)
                self.sort_order = (
                    0 if existing_max is None else existing_max + 1
                )
        super().save(*args, **kwargs)

    def get_ordering_queryset(self) -> models.QuerySet[SortableModel]:
        """
        Get the queryset used for ordering operations.

        Override this method to customize ordering scope (e.g., per category).

        Returns:
            QuerySet of all instances to consider for ordering
        """
        model_class = self.__class__
        return model_class._default_manager.all()

    @staticmethod
    def get_max_sort_order(qs: models.QuerySet[SortableModel]) -> int | None:
        """
        Get the maximum sort_order value from a queryset.

        Args:
            qs: QuerySet to check for maximum sort_order

        Returns:
            Maximum sort_order value, or None if queryset is empty
        """
        return qs.aggregate(Max("sort_order"))["sort_order__max"]

    def move_up(self) -> None:
        """
        Move this item up in the sort order (decrease sort_order by 1).

        Swaps sort_order with the previous item in a transaction.
        """
        if self.sort_order is not None and self.sort_order > 0:
            with transaction.atomic():
                qs = self.get_ordering_queryset().select_for_update()
                try:
                    prev_item = qs.get(sort_order=self.sort_order - 1)
                    prev_item.sort_order, self.sort_order = (
                        self.sort_order,
                        prev_item.sort_order,
                    )
                    prev_item.save(update_fields=["sort_order"])
                    self.save(update_fields=["sort_order"])
                except self.__class__.DoesNotExist:
                    pass

    def move_down(self) -> None:
        """
        Move this item down in the sort order (increase sort_order by 1).

        Swaps sort_order with the next item in a transaction.
        """
        if self.sort_order is not None:
            with transaction.atomic():
                qs = self.get_ordering_queryset().select_for_update()
                next_item = qs.filter(sort_order__gt=self.sort_order).first()
                if next_item:
                    next_item.sort_order, self.sort_order = (
                        self.sort_order,
                        next_item.sort_order,
                    )
                    next_item.save(update_fields=["sort_order"])
                    self.save(update_fields=["sort_order"])

    @transaction.atomic
    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """
        Delete the instance and reorder remaining items.

        Decrements sort_order for all items after this one to maintain
        continuous ordering.

        Returns:
            Tuple of (number of objects deleted, dict of deletions per type)
        """
        if self.sort_order is not None:
            qs = self.get_ordering_queryset()
            qs.filter(sort_order__gt=self.sort_order).update(
                sort_order=F("sort_order") - 1
            )
        return super().delete(*args, **kwargs)


class TimeStampMixinModel(models.Model):
    """
    Abstract model that adds created_at and updated_at timestamps.
    """

    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta(TypedModelMeta):
        abstract = True
        indexes = [
            BTreeIndex(fields=["created_at"], name="%(class)s_created_at_ix"),
            BTreeIndex(fields=["updated_at"], name="%(class)s_updated_at_ix"),
        ]

    def get_duration_since_created(self):
        return timezone.now() - self.created_at

    def get_duration_since_updated(self):
        return timezone.now() - self.updated_at


class UUIDModel(models.Model):
    """
    Abstract model that adds a unique UUID field.
    """

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    class Meta(TypedModelMeta):
        abstract = True


class PublishedQuerySet(models.QuerySet):
    """
    QuerySet for PublishableModel that provides a published() filter.
    """

    def published(self):
        today = timezone.now()
        return self.filter(
            Q(published_at__lte=today, is_published=True)
            | Q(published_at__isnull=True, is_published=True)
        )


PublishableManager = models.Manager.from_queryset(PublishedQuerySet)


class PublishableModel(models.Model):
    """
    Abstract model that adds published_at and is_published fields.
    """

    published_at = models.DateTimeField(
        _("Published At"), null=True, blank=True
    )
    is_published = models.BooleanField(_("Is Published"), default=False)

    objects: PublishableManager = PublishableManager()

    class Meta(TypedModelMeta):
        abstract = True
        indexes = [
            BTreeIndex(
                fields=["published_at"], name="%(class)s_published_at_ix"
            ),
            BTreeIndex(
                fields=["is_published"], name="%(class)s_is_published_ix"
            ),
        ]

    def save(self, *args, **kwargs):
        if self.is_published and self.published_at is None:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)


class MetaDataModel(models.Model):
    """
    Abstract model that adds private_metadata and metadata JSON fields.
    """

    private_metadata = JSONField(
        blank=True, default=dict, encoder=DjangoJSONEncoder
    )
    metadata = JSONField(blank=True, default=dict, encoder=DjangoJSONEncoder)

    class Meta(TypedModelMeta):
        indexes = [
            GinIndex(fields=["private_metadata"], name="%(class)s_p_meta_ix"),
            GinIndex(fields=["metadata"], name="%(class)s_meta_ix"),
        ]
        abstract = True

    def save(self, *args, **kwargs):
        if not self.private_metadata:
            self.private_metadata = {}
        if not self.metadata:
            self.metadata = {}
        super().save(*args, **kwargs)

    def get_value_from_private_metadata(self, key: str, default: Any = None):
        return self.private_metadata.get(key, default)

    def store_value_in_private_metadata(self, items: dict):
        if items:
            for key, value in items.items():
                self.private_metadata[key] = value
            self.save(update_fields=["private_metadata"])
            self.refresh_from_db(fields=["private_metadata"])

    def clear_private_metadata(self):
        self.private_metadata = {}

    def delete_value_from_private_metadata(self, key: str):
        if key in self.private_metadata:
            del self.private_metadata[key]

    def get_value_from_metadata(self, key: str, default: Any = None):
        return self.metadata.get(key, default)

    def store_value_in_metadata(self, items: dict):
        if items:
            for key, value in items.items():
                self.metadata[key] = value
            self.save(update_fields=["metadata"])
            self.refresh_from_db(fields=["metadata"])

    def clear_metadata(self):
        self.metadata = {}

    def delete_value_from_metadata(self, key: str):
        if key in self.metadata:
            del self.metadata[key]


class SoftDeleteMixin(models.Model):
    """
    Abstract model that adds soft delete functionality.

    Provides soft delete capability where records are marked as deleted
    rather than being removed from the database.
    """

    deleted_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)

    class Meta(TypedModelMeta):
        abstract = True

    def delete(
        self, using: str | None = None, keep_parents: bool = False
    ) -> tuple[int, dict[str, int]]:
        """
        Soft delete the instance by marking it as deleted.

        Uses update_fields for better performance instead of full model save.

        Args:
            using: Database alias to use
            keep_parents: Whether to keep parent records (unused in soft delete)

        Returns:
            Tuple of (count, {model_label: count}) to match Django's delete signature
        """
        self.deleted_at = timezone.now()
        self.is_deleted = True
        self.save(update_fields=["deleted_at", "is_deleted"])
        return (1, {self._meta.label: 1})

    def restore(self) -> None:
        """
        Restore a soft-deleted instance.

        Uses update_fields for better performance.
        """
        self.deleted_at = None
        self.is_deleted = False
        self.save(update_fields=["deleted_at", "is_deleted"])


class SoftDeleteQuerySet(models.QuerySet):
    """
    QuerySet that overrides delete() to perform soft delete.

    Provides methods for soft delete, restore, and hard delete operations.
    """

    def delete(self) -> tuple[int, dict[str, int]]:
        """
        Soft delete all instances in the queryset.

        Returns:
            Tuple of (number updated, dict of updates per model)
        """
        count = super().update(deleted_at=timezone.now(), is_deleted=True)
        return (count, {self.model._meta.label: count})

    def restore(self) -> int:
        """
        Restore all soft-deleted instances in the queryset.

        Returns:
            Number of instances restored
        """
        return super().update(deleted_at=None, is_deleted=False)

    def hard_delete(self) -> tuple[int, dict[str, int]]:
        """
        Permanently delete all instances in the queryset.

        Returns:
            Tuple of (number deleted, dict of deletions per model)
        """
        return super().delete()


class SoftDeleteManager(models.Manager):
    """
    Manager that filters out soft-deleted items by default.

    Provides methods to access all records including deleted ones,
    or only deleted records.
    """

    def get_queryset(self) -> SoftDeleteQuerySet:
        """
        Get the default queryset excluding soft-deleted items.

        Returns:
            QuerySet with is_deleted=False filter applied
        """
        return SoftDeleteQuerySet(self.model, using=self._db).exclude(
            is_deleted=True
        )

    def all_with_deleted(self) -> SoftDeleteQuerySet:
        """
        Get all records including soft-deleted ones.

        Returns:
            Unfiltered QuerySet
        """
        return SoftDeleteQuerySet(self.model, using=self._db)

    def deleted_only(self) -> SoftDeleteQuerySet:
        """
        Get only soft-deleted records.

        Returns:
            QuerySet with is_deleted=True filter applied
        """
        return SoftDeleteQuerySet(self.model, using=self._db).filter(
            is_deleted=True
        )


class SoftDeleteModel(SoftDeleteMixin, models.Model):
    class Meta(TypedModelMeta):
        abstract = True
