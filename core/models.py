import uuid
from typing import Any
from typing import TypeVar

from django.contrib.postgres.indexes import GinIndex
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db import transaction
from django.db.models import F
from django.db.models import JSONField
from django.db.models import Max
from django.db.models import Q
from django.db.models import QuerySet
from django.utils import timezone as tz
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _


class SortableModel(models.Model):
    sort_order = models.IntegerField(
        _("Sort Order"), editable=False, db_index=True, null=True
    )

    class Meta:
        abstract = True

    def get_ordering_queryset(self) -> QuerySet[Any]:
        raise NotImplementedError("Unknown ordering queryset")

    @staticmethod
    def get_max_sort_order(qs) -> int:
        existing_max = qs.aggregate(Max("sort_order"))
        existing_max = existing_max.get("sort_order__max")
        return existing_max

    def save(self, *args, **kwargs) -> None:
        if self.pk is None:
            qs = self.get_ordering_queryset()
            existing_max = self.get_max_sort_order(qs)
            self.sort_order = 0 if existing_max is None else existing_max + 1
        super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs) -> None:
        if self.sort_order is not None:
            qs = self.get_ordering_queryset()
            qs.filter(sort_order__gt=self.sort_order).update(
                sort_order=F("sort_order") - 1
            )
        super().delete(*args, **kwargs)


class TimeStampMixinModel(models.Model):
    created_at = models.DateTimeField(
        _("Created At"), null=False, blank=False, default=tz.now
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    class Meta:
        abstract = True


T = TypeVar("T", bound="PublishableModel")


class PublishedQuerySet(models.QuerySet[T]):
    def published(self):
        today = now()
        return self.filter(
            Q(published_at__lte=today) | Q(published_at__isnull=True),
            is_published=True,
        )


PublishableManager = models.Manager.from_queryset(PublishedQuerySet)


class PublishableModel(models.Model):
    published_at = models.DateTimeField(_("Published At"), null=True, blank=True)
    is_published = models.BooleanField(_("Is Published"), default=False)

    objects: Any = PublishableManager()

    class Meta:
        abstract = True

    @property
    def is_visible(self):
        return self.is_published and (
            self.published_at is None or self.published_at <= now()
        )


class ModelWithMetadata(models.Model):
    private_metadata = JSONField(
        blank=True, null=True, default=dict, encoder=DjangoJSONEncoder
    )
    metadata = JSONField(blank=True, null=True, default=dict, encoder=DjangoJSONEncoder)

    class Meta:
        indexes = [
            GinIndex(fields=["private_metadata"], name="%(class)s_p_meta_idx"),
            GinIndex(fields=["metadata"], name="%(class)s_meta_idx"),
        ]
        abstract = True

    def get_value_from_private_metadata(self, key: str, default: Any = None) -> Any:
        return self.private_metadata.get(key, default)

    def store_value_in_private_metadata(self, items: dict):
        if not self.private_metadata:
            self.private_metadata = {}
        self.private_metadata.update(items)

    def clear_private_metadata(self):
        self.private_metadata = {}

    def delete_value_from_private_metadata(self, key: str):
        if key in self.private_metadata:
            del self.private_metadata[key]

    def get_value_from_metadata(self, key: str, default: Any = None) -> Any:
        return self.metadata.get(key, default)

    def store_value_in_metadata(self, items: dict):
        if not self.metadata:
            self.metadata = {}
        self.metadata.update(items)

    def clear_metadata(self):
        self.metadata = {}

    def delete_value_from_metadata(self, key: str):
        if key in self.metadata:
            del self.metadata[key]
