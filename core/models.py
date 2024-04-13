import json
import uuid
from typing import Any
from typing import TypeVar

from django.contrib.postgres.indexes import BTreeIndex
from django.contrib.postgres.indexes import GinIndex
from django.contrib.sites.models import Site
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db import transaction
from django.db.models import F
from django.db.models import JSONField
from django.db.models import Max
from django.db.models import Q
from django.db.models import QuerySet
from django.utils import timezone as tz
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.caches import cache_instance
from core.enum import SettingsValueTypeEnum


class Settings(models.Model):
    site = models.ForeignKey(
        Site, on_delete=models.CASCADE, verbose_name=_("Site"), related_name="settings"
    )
    key = models.CharField(_("Key"), max_length=255, unique=True)
    value = models.TextField(_("Value"))
    value_type = models.CharField(
        _("Value Type"),
        max_length=50,
        choices=SettingsValueTypeEnum.choices,
        default=SettingsValueTypeEnum.STRING,
    )
    description = models.TextField(_("Description"), blank=True, null=True)
    is_public = models.BooleanField(_("Is Public"), default=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Setting")
        verbose_name_plural = _("Settings")
        unique_together = ("site", "key")
        indexes = [models.Index(fields=["value_type"], name="settings_value_type_idx")]

    def __str__(self):
        return f"{self.site} - {self.key}: {self.value} - {self.value_type}"

    def save(self, *args, **kwargs):
        if not self.pk and not self.key.startswith("APP_"):
            raise ValidationError(
                _("Setting key must start with 'APP_'."), code="invalid_key"
            )
        self.validate_value(self.value, self.value_type)
        super(Settings, self).save(*args, **kwargs)
        cache_instance.delete(f"{self.site.id}_{self.key}")

    def set_value(self, value) -> None:
        self.value = json.dumps(value, cls=DjangoJSONEncoder)
        self.value_type = self.python_type_to_enum(type(value).__name__)

    def get_value(self) -> Any:
        value = json.loads(self.value)
        if self.value_type == SettingsValueTypeEnum.INTEGER:
            return int(value)
        elif self.value_type == SettingsValueTypeEnum.BOOLEAN:
            if isinstance(value, bool):
                return value
            return value.lower() in ("true", "1")
        elif self.value_type in [
            SettingsValueTypeEnum.DICTIONARY,
            SettingsValueTypeEnum.LIST,
        ]:
            return value
        elif self.value_type == SettingsValueTypeEnum.FLOAT:
            return float(value)
        else:
            return value

    @staticmethod
    def python_type_to_enum(python_type: str) -> str:
        type_mapping = {
            "str": SettingsValueTypeEnum.STRING,
            "int": SettingsValueTypeEnum.INTEGER,
            "bool": SettingsValueTypeEnum.BOOLEAN,
            "dict": SettingsValueTypeEnum.DICTIONARY,
            "list": SettingsValueTypeEnum.LIST,
            "float": SettingsValueTypeEnum.FLOAT,
        }
        return type_mapping.get(python_type, SettingsValueTypeEnum.STRING)

    @staticmethod
    def validate_value(value: str, value_type: str) -> None:
        if value_type == SettingsValueTypeEnum.BOOLEAN:
            if isinstance(value, bool):
                value = str(value).lower()
            if value.lower() not in ("true", "false", "1", "0"):
                raise ValidationError(_("Value is not a valid boolean."))

        if value_type == SettingsValueTypeEnum.STRING:
            return

        try:
            loaded_value = json.loads(value)
            if value_type == SettingsValueTypeEnum.STRING and not isinstance(
                loaded_value, str
            ):
                raise ValidationError(_("Value does not match String type."))
            elif value_type == SettingsValueTypeEnum.INTEGER and not isinstance(
                loaded_value, int
            ):
                raise ValidationError(_("Value does not match Integer type."))
            elif value_type == SettingsValueTypeEnum.FLOAT and not isinstance(
                loaded_value, float
            ):
                raise ValidationError(_("Value does not match Float type."))
            elif value_type == SettingsValueTypeEnum.BOOLEAN and not isinstance(
                loaded_value, bool
            ):
                raise ValidationError(_("Value does not match Boolean type."))
            elif value_type == SettingsValueTypeEnum.DICTIONARY and not isinstance(
                loaded_value, dict
            ):
                raise ValidationError(_("Value does not match Dictionary type."))
            elif value_type == SettingsValueTypeEnum.LIST and not isinstance(
                loaded_value, list
            ):
                raise ValidationError(_("Value does not match List type."))
        except json.JSONDecodeError:
            raise ValidationError(_("Value is not valid JSON."))

    @classmethod
    def get_setting(cls, key: str, site_id: int, default=None) -> Any:
        cached_value = cache_instance.get(f"{site_id}_{key}")
        if cached_value is not None:
            return json.loads(cached_value)["value"]

        try:
            setting = cls.objects.get(key=key)
            cache_value = json.dumps(
                {"value": setting.get_value(), "type": setting.value_type}
            )
            cache_instance.set(key, cache_value)
            return setting.get_value()
        except cls.DoesNotExist:
            return default

    @classmethod
    def set_setting(cls, key: str, value, site_id: int) -> None:
        setting, created = cls.objects.get_or_create(key=key, site_id=site_id)
        setting.set_value(value)
        setting.save()


class SortableModel(models.Model):
    sort_order = models.IntegerField(
        _("Sort Order"), editable=False, db_index=True, null=True
    )

    class Meta(TypedModelMeta):
        abstract = True
        indexes = [
            BTreeIndex(fields=["sort_order"], name="%(class)s_sort_order_idx"),
        ]

    def get_ordering_queryset(self) -> QuerySet[Any]:
        model_class = self.__class__
        return model_class.objects.all()

    @staticmethod
    def get_max_sort_order(qs) -> int:
        return qs.aggregate(Max("sort_order"))["sort_order__max"] or 0

    def save(self, *args, **kwargs) -> None:
        if self.pk is None:
            qs = self.get_ordering_queryset()
            existing_max = self.get_max_sort_order(qs)
            self.sort_order = 0 if existing_max is None else existing_max + 1
        super().save(*args, **kwargs)

    def move_up(self):
        if self.sort_order > 0:
            qs = self.get_ordering_queryset()
            prev_item = qs.get(sort_order=self.sort_order - 1)
            prev_item.sort_order, self.sort_order = (
                self.sort_order,
                prev_item.sort_order,
            )
            prev_item.save()
            self.save()

    def move_down(self):
        qs = self.get_ordering_queryset()
        next_item = qs.filter(sort_order__gt=self.sort_order).first()
        if next_item:
            next_item.sort_order, self.sort_order = (
                self.sort_order,
                next_item.sort_order,
            )
            next_item.save()
            self.save()

    @transaction.atomic
    def delete(self, *args, **kwargs) -> None:
        if self.sort_order is not None:
            qs = self.get_ordering_queryset()
            qs.filter(sort_order__gt=self.sort_order).update(
                sort_order=F("sort_order") - 1
            )
        super().delete(*args, **kwargs)


class TimeStampMixinModel(models.Model):
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)
    updated_at = models.DateTimeField(_("Updated At"), auto_now=True)

    class Meta(TypedModelMeta):
        abstract = True
        indexes = [
            BTreeIndex(fields=["created_at"], name="%(class)s_created_at_idx"),
            BTreeIndex(fields=["updated_at"], name="%(class)s_updated_at_idx"),
        ]

    def get_duration_since_created(self):
        return tz.now() - self.created_at

    def get_duration_since_updated(self):
        return tz.now() - self.updated_at


class UUIDModel(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    class Meta(TypedModelMeta):
        abstract = True


T = TypeVar("T", bound="PublishableModel")


class PublishedQuerySet(models.QuerySet[T]):
    def published(self):
        today = tz.now()
        return self.filter(
            Q(published_at__lte=today, is_published=True)
            | Q(published_at__isnull=True, is_published=True)
        )


PublishableManager = models.Manager.from_queryset(PublishedQuerySet)


class PublishableModel(models.Model):
    published_at = models.DateTimeField(_("Published At"), null=True, blank=True)
    is_published = models.BooleanField(_("Is Published"), default=False)

    objects: Any = PublishableManager()

    class Meta(TypedModelMeta):
        abstract = True
        indexes = [
            BTreeIndex(fields=["published_at"], name="%(class)s_published_at_idx"),
            BTreeIndex(fields=["is_published"], name="%(class)s_is_published_idx"),
        ]

    @property
    def is_visible(self):
        return self.is_published and (
            self.published_at is None or self.published_at <= tz.now()
        )


class ModelWithMetadata(models.Model):
    private_metadata = JSONField(blank=True, default=dict, encoder=DjangoJSONEncoder)
    metadata = JSONField(blank=True, default=dict, encoder=DjangoJSONEncoder)

    class Meta(TypedModelMeta):
        indexes = [
            GinIndex(fields=["private_metadata"], name="%(class)s_p_meta_idx"),
            GinIndex(fields=["metadata"], name="%(class)s_meta_idx"),
        ]
        abstract = True

    def save(self, *args, **kwargs):
        if not self.private_metadata:
            self.private_metadata = {}
        if not self.metadata:
            self.metadata = {}
        super().save(*args, **kwargs)

    def get_value_from_private_metadata(self, key: str, default: Any = None) -> Any:
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

    def get_value_from_metadata(self, key: str, default: Any = None) -> Any:
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
    deleted_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)

    class Meta(TypedModelMeta):
        abstract = True

    def delete(self, using=None, keep_parents=False):
        self.deleted_at = tz.now()
        self.is_deleted = True
        self.save()

    def restore(self):
        self.deleted_at = None
        self.is_deleted = False
        self.save()


class SoftDeleteQuerySet(models.QuerySet):
    def delete(self):
        return super().update(deleted_at=tz.now(), is_deleted=True)

    def restore(self):
        return super().update(deleted_at=None, is_deleted=False)

    def hard_delete(self):
        return super().delete()


class SoftDeleteManager(models.Manager):
    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).exclude(is_deleted=True)

    def all_with_deleted(self):
        return SoftDeleteQuerySet(self.model, using=self._db)

    def deleted_only(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=True)


class SoftDeleteModel(SoftDeleteMixin, models.Model):
    class Meta(TypedModelMeta):
        abstract = True
