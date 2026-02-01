import logging
from collections.abc import Iterable
from typing import Any, TypedDict

from django.conf import settings
from django.db import models
from django.db.models.fields import Field
from meilisearch.models.task import Task, TaskInfo

from meili._client import client as _client
from meili.dataclasses import MeiliIndexSettings
from meili.querysets import IndexQuerySet

logger = logging.getLogger(__name__)


class MeiliGeo(TypedDict):
    lat: float | str
    lng: float | str


class _Meili(TypedDict):
    primary_key: str
    index_name: str
    displayed_fields: Iterable[str] | None
    searchable_fields: Iterable[str] | None
    filterable_fields: Iterable[str] | None
    sortable_fields: Iterable[str] | None
    supports_geo: bool
    tasks: list[Task | TaskInfo]
    include_pk_in_search: bool


class IndexMixin(models.Model):
    meilisearch: IndexQuerySet
    _meilisearch: _Meili

    objects: models.Manager

    class Meta:
        abstract = True

    class MeiliMeta:
        displayed_fields: Iterable[str] | None = None
        searchable_fields: Iterable[str] | None = None
        filterable_fields: Iterable[str] | None = None
        sortable_fields: Iterable[str] | None = None
        ranking_rules: Iterable[str] | None = None
        stop_words: Iterable[str] | None = None
        synonyms: dict[str, list[str]] | None = None
        distinct_attribute: str | None = None
        typo_tolerance: dict[str, Any] | None = None
        faceting: dict[str, Any] | None = None
        pagination: dict[str, Any] | None = None
        supports_geo: bool = False
        index_name: str | None = None
        primary_key: str = "pk"
        include_pk_in_search: bool = False

    @classmethod
    def get_meili_settings(cls) -> MeiliIndexSettings:
        """Extract MeiliIndexSettings from the model's MeiliMeta configuration."""
        displayed_fields = getattr(cls.MeiliMeta, "displayed_fields", None)
        searchable_fields = getattr(cls.MeiliMeta, "searchable_fields", None)
        filterable_fields = getattr(cls.MeiliMeta, "filterable_fields", None)
        sortable_fields = getattr(cls.MeiliMeta, "sortable_fields", None)
        ranking_rules = getattr(cls.MeiliMeta, "ranking_rules", None)
        stop_words = getattr(cls.MeiliMeta, "stop_words", None)
        synonyms = getattr(cls.MeiliMeta, "synonyms", None)
        distinct_attribute = getattr(cls.MeiliMeta, "distinct_attribute", None)
        typo_tolerance = getattr(cls.MeiliMeta, "typo_tolerance", None)
        faceting = getattr(cls.MeiliMeta, "faceting", None)
        pagination = getattr(cls.MeiliMeta, "pagination", None)
        supports_geo = getattr(cls.MeiliMeta, "supports_geo", False)

        if supports_geo:
            filterable_fields = ("_geo",) + (filterable_fields or ())
            sortable_fields = ("_geo",) + (sortable_fields or ())

        return MeiliIndexSettings(
            displayed_fields=displayed_fields,
            searchable_fields=searchable_fields,
            filterable_fields=filterable_fields,
            sortable_fields=sortable_fields,
            ranking_rules=ranking_rules,
            stop_words=stop_words,
            synonyms=synonyms,
            distinct_attribute=distinct_attribute,
            typo_tolerance=typo_tolerance,
            faceting=faceting,
            pagination=pagination,
        )

    @classmethod
    def update_meili_settings(cls) -> None:
        """Update Meilisearch index settings from the model's MeiliMeta configuration."""
        index_name = cls._meilisearch["index_name"]
        index_settings = cls.get_meili_settings()

        _client.with_settings(
            index_name=index_name,
            index_settings=index_settings,
        )

        if _client.tasks:
            for task in _client.tasks:
                task_uid = getattr(task, "task_uid", None) or getattr(
                    task, "uid", None
                )
                if task_uid:
                    finished = _client.wait_for_task(task_uid)
                    if finished.status == "failed":
                        raise Exception(finished.error)
            _client.flush_tasks()

    def __init_subclass__(cls) -> None:
        index_name = getattr(cls.MeiliMeta, "index_name", cls.__name__)
        primary_key = getattr(cls.MeiliMeta, "primary_key", "pk")
        supports_geo = getattr(cls.MeiliMeta, "supports_geo", False)
        include_pk_in_search = getattr(
            cls.MeiliMeta, "include_pk_in_search", False
        )

        index_settings = cls.get_meili_settings()

        if settings.MEILISEARCH.get("OFFLINE", False):
            cls._meilisearch = _Meili(
                primary_key=primary_key,
                index_name=index_name,
                displayed_fields=index_settings.displayed_fields,
                searchable_fields=index_settings.searchable_fields,
                filterable_fields=index_settings.filterable_fields,
                sortable_fields=index_settings.sortable_fields,
                supports_geo=supports_geo,
                include_pk_in_search=include_pk_in_search,
                tasks=[],
            )
        else:
            try:
                _client.create_index(index_name, primary_key).with_settings(
                    index_name=index_name,
                    index_settings=index_settings,
                )
            except Exception as e:
                logger.error(f"Failed to create index {index_name}: {e}")
                pass

        cls._meilisearch = _Meili(
            primary_key=primary_key,
            index_name=index_name,
            displayed_fields=index_settings.displayed_fields,
            searchable_fields=index_settings.searchable_fields,
            filterable_fields=index_settings.filterable_fields,
            sortable_fields=index_settings.sortable_fields,
            supports_geo=supports_geo,
            include_pk_in_search=include_pk_in_search,
            tasks=[task for task in _client.tasks],
        )
        _client.flush_tasks()

    @classmethod
    def get_meilisearch(cls):
        """Return a fresh IndexQuerySet instance to avoid state accumulation."""
        return IndexQuerySet(cls)

    class _MeilisearchDescriptor:
        """Descriptor that returns a fresh IndexQuerySet instance on each access."""

        def __get__(self, obj, objtype=None):
            if objtype is None:
                objtype = type(obj)
            return IndexQuerySet(objtype)

    meilisearch = _MeilisearchDescriptor()

    def meili_filter(self) -> bool:
        """
        A function to decide if the model should be added to meilisearch.

        For example, if a post model could be a draft and that draft shouldn't
        be in the search database, then this filter can make sure its not added.
        """
        return True

    @classmethod
    def get_additional_meili_fields(cls):
        return {}

    def meili_serialize(self):
        """
        How to serialize the model to a dictionary to be used by meilisearch.

        By default, uses django.core.serializers.serialize and json.loads
        Only serializes fields defined in displayed_fields, searchable_fields, and filterable_fields.
        """
        fields = {
            *(self.MeiliMeta.displayed_fields or []),
            *(self.MeiliMeta.searchable_fields or []),
            *(self.MeiliMeta.filterable_fields or []),
        }

        # Try fast path first - direct field access
        data = {}
        for field_name in fields:
            try:
                value = getattr(self, field_name, None)
                # Handle common types
                if value is None:
                    data[field_name] = None
                elif isinstance(value, (str, int, float, bool)):
                    data[field_name] = value
                elif hasattr(value, "isoformat"):  # datetime/date
                    data[field_name] = value.isoformat()
                else:
                    data[field_name] = str(value)
            except AttributeError:
                data[field_name] = None

        # Add additional fields
        additional_fields = self.get_additional_meili_fields()
        for field_name, value_getter in additional_fields.items():
            try:
                data[field_name] = value_getter(self)
            except (AttributeError, TypeError):
                data[field_name] = None

        if getattr(self.MeiliMeta, "include_pk_in_search", False):
            field = self._meta.get_field(self.MeiliMeta.primary_key)
            if isinstance(field, Field):
                data[self.MeiliMeta.primary_key] = field.value_to_string(self)
            else:
                data[self.MeiliMeta.primary_key] = str(
                    getattr(self, self.MeiliMeta.primary_key)
                )

        return data

    def meili_geo(self) -> MeiliGeo:
        raise ValueError("Model does not support geolocation")
