import uuid
from gettext import gettext as _
from typing import Any

from django.db import models
from django.db import transaction
from django.db.models import F
from django.db.models import Max
from django.db.models import Q
from django.db.models import QuerySet
from django.utils import timezone as tz
from django.utils.timezone import now


class SortableModel(models.Model):
    sort_order = models.IntegerField(editable=False, db_index=True, null=True)

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


class PublishedQuerySet(models.QuerySet):
    def published(self):
        today = now()
        return self.filter(
            Q(published_at__lte=today) | Q(published_at__isnull=True),
            is_published=True,
        )


class PublishableModel(models.Model):
    published_at = models.DateTimeField(blank=True, null=True)
    is_published = models.BooleanField(default=False)

    objects: Any = models.Manager.from_queryset(PublishedQuerySet)()

    class Meta:
        abstract = True

    @property
    def is_visible(self):
        return self.is_published and (
            self.published_at is None or self.published_at <= now()
        )
