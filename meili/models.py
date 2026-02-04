"""
Meilisearch integration models and mixins.

Provides the IndexMixin abstract base class that enables Django models
to be automatically indexed in Meilisearch.
"""

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
    """Geographic coordinates for Meilisearch geo-search."""

    lat: float | str
    lng: float | str


class _Meili(TypedDict):
    """Internal Meilisearch configuration for a model."""

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
    """
    Abstract base class for Meilisearch-indexed Django models.

    Provides automatic indexing via signals and a queryset-like interface
    for searching via the `meilisearch` descriptor.

    Usage:
        class Product(IndexMixin):
            name = models.CharField(max_length=255)
            price = models.DecimalField(...)

            class MeiliMeta:
                filterable_fields = ("name", "price")
                searchable_fields = ("name", "description")
                sortable_fields = ("price", "created_at")

        # Search products
        results = Product.meilisearch.filter(price__gte=100).search("laptop")
    """

    meilisearch: IndexQuerySet
    _meilisearch: _Meili

    objects: models.Manager

    class Meta:
        abstract = True

    class MeiliMeta:
        """
        Configuration class for Meilisearch index settings.

        Attributes:
            displayed_fields: Fields returned in search results
            searchable_fields: Fields searched for query matches
            filterable_fields: Fields available for filtering
            sortable_fields: Fields available for sorting
            ranking_rules: Custom ranking rules
            stop_words: Words to ignore in search
            synonyms: Synonym mappings
            distinct_attribute: Field for deduplication
            typo_tolerance: Typo tolerance configuration
            faceting: Faceting configuration
            pagination: Pagination limits
            supports_geo: Enable geo-search support
            index_name: Custom index name (defaults to model name)
            primary_key: Document primary key field
            include_pk_in_search: Include PK in searchable fields
            search_cutoff_ms: Maximum search time in milliseconds
        """

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
        search_cutoff_ms: int | None = None

    @classmethod
    def get_meili_settings(cls) -> MeiliIndexSettings:
        """Extract MeiliIndexSettings from the model's MeiliMeta configuration."""
        meta = cls.MeiliMeta

        displayed_fields = getattr(meta, "displayed_fields", None)
        searchable_fields = getattr(meta, "searchable_fields", None)
        filterable_fields = getattr(meta, "filterable_fields", None)
        sortable_fields = getattr(meta, "sortable_fields", None)
        ranking_rules = getattr(meta, "ranking_rules", None)
        stop_words = getattr(meta, "stop_words", None)
        synonyms = getattr(meta, "synonyms", None)
        distinct_attribute = getattr(meta, "distinct_attribute", None)
        typo_tolerance = getattr(meta, "typo_tolerance", None)
        faceting = getattr(meta, "faceting", None)
        pagination = getattr(meta, "pagination", None)
        supports_geo = getattr(meta, "supports_geo", False)
        search_cutoff_ms = getattr(meta, "search_cutoff_ms", None)

        # Add geo fields if geo-search is enabled
        if supports_geo:
            filterable_fields = ("_geo",) + tuple(filterable_fields or ())
            sortable_fields = ("_geo",) + tuple(sortable_fields or ())

        return MeiliIndexSettings(
            displayed_fields=list(displayed_fields)
            if displayed_fields
            else None,
            searchable_fields=list(searchable_fields)
            if searchable_fields
            else None,
            filterable_fields=list(filterable_fields)
            if filterable_fields
            else None,
            sortable_fields=list(sortable_fields) if sortable_fields else None,
            ranking_rules=list(ranking_rules) if ranking_rules else None,
            stop_words=list(stop_words) if stop_words else None,
            synonyms=synonyms,
            distinct_attribute=distinct_attribute,
            typo_tolerance=typo_tolerance,
            faceting=faceting,
            pagination=pagination,
            search_cutoff_ms=search_cutoff_ms,
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
                        raise Exception(
                            f"Failed to update settings: {finished.error}"
                        )
            _client.flush_tasks()

    def __init_subclass__(cls) -> None:
        """Initialize Meilisearch configuration when a subclass is created."""
        super().__init_subclass__()

        # Skip abstract models
        if getattr(cls._meta, "abstract", False):
            return

        # Note: Due to Python's MRO, __init_subclass__ may not be called
        # for models that inherit from multiple mixins where another mixin
        # comes before IndexMixin. The _meilisearch attribute will be
        # initialized in MeiliConfig.ready() for such cases.

        meta = cls.MeiliMeta
        index_name = getattr(meta, "index_name", None) or cls.__name__
        primary_key = getattr(meta, "primary_key", "pk")
        supports_geo = getattr(meta, "supports_geo", False)
        include_pk_in_search = getattr(meta, "include_pk_in_search", False)

        try:
            index_settings = cls.get_meili_settings()
        except Exception as e:
            logger.warning(
                f"Failed to get meili settings for {cls.__name__}: {e}"
            )
            return

        # Initialize _meilisearch configuration
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

        # Skip index creation in offline mode
        if settings.MEILISEARCH.get("OFFLINE", False):
            return

        # Create index and apply settings
        try:
            _client.create_index(index_name, primary_key).with_settings(
                index_name=index_name,
                index_settings=index_settings,
            )

            # Store tasks for reference
            cls._meilisearch = _Meili(
                **{**cls._meilisearch, "tasks": list(_client.tasks)}
            )
            _client.flush_tasks()

        except Exception as e:
            logger.warning(f"Failed to initialize index {index_name}: {e}")

    class _MeilisearchDescriptor:
        """Descriptor that returns a fresh IndexQuerySet instance on each access."""

        def __get__(self, obj, objtype=None):
            if objtype is None:
                objtype = type(obj)
            return IndexQuerySet(objtype)

    meilisearch = _MeilisearchDescriptor()

    @classmethod
    def get_meilisearch(cls) -> IndexQuerySet:
        """Return a fresh IndexQuerySet instance."""
        return IndexQuerySet(cls)

    @classmethod
    def get_meilisearch_queryset(cls):
        """
        Return an optimized Django queryset for bulk indexing.

        Override this method to add select_related, prefetch_related,
        or annotations needed for serialization.

        Example:
            @classmethod
            def get_meilisearch_queryset(cls):
                return cls.objects.select_related('category').annotate(
                    review_count=Count('reviews')
                )
        """
        return cls.objects.all()

    def meili_filter(self) -> bool:
        """
        Determine if this instance should be indexed.

        Override to implement conditional indexing logic.

        Example:
            def meili_filter(self) -> bool:
                return self.is_published and not self.is_deleted
        """
        return True

    @classmethod
    def get_additional_meili_fields(cls) -> dict[str, callable]:
        """
        Return additional fields to include in the indexed document.

        Override to add computed fields not directly on the model.

        Example:
            @classmethod
            def get_additional_meili_fields(cls):
                return {
                    'full_name': lambda obj: f"{obj.first_name} {obj.last_name}",
                    'category_name': lambda obj: obj.category.name if obj.category else None,
                }
        """
        return {}

    def meili_serialize(self) -> dict:
        """
        Serialize the model instance for Meilisearch indexing.

        Returns a dictionary containing all fields defined in displayed_fields,
        searchable_fields, and filterable_fields, plus any additional fields.
        """
        meta = self.MeiliMeta

        # Collect all fields to serialize
        fields = set()
        if meta.displayed_fields:
            fields.update(meta.displayed_fields)
        if meta.searchable_fields:
            fields.update(meta.searchable_fields)
        if meta.filterable_fields:
            fields.update(meta.filterable_fields)

        # Serialize each field
        data = {}
        for field_name in fields:
            # Skip internal Meilisearch fields
            if field_name.startswith("_"):
                continue

            try:
                value = getattr(self, field_name, None)
                data[field_name] = self._serialize_value(value)
            except AttributeError:
                data[field_name] = None

        # Add additional computed fields
        additional_fields = self.get_additional_meili_fields()
        for field_name, value_getter in additional_fields.items():
            try:
                data[field_name] = self._serialize_value(value_getter(self))
            except AttributeError, TypeError:
                data[field_name] = None

        # Optionally include primary key
        if getattr(meta, "include_pk_in_search", False):
            pk_field = meta.primary_key
            field = self._meta.get_field(pk_field)
            if isinstance(field, Field):
                data[pk_field] = field.value_to_string(self)
            else:
                data[pk_field] = str(getattr(self, pk_field))

        return data

    def _serialize_value(self, value: Any) -> Any:
        """Serialize a single value for Meilisearch."""
        if value is None:
            return None
        elif isinstance(value, str | int | float | bool):
            return value
        elif hasattr(value, "isoformat"):  # datetime/date
            return value.isoformat()
        elif isinstance(value, list | tuple):
            return [self._serialize_value(v) for v in value]
        elif isinstance(value, dict):
            return {k: self._serialize_value(v) for k, v in value.items()}
        else:
            return str(value)

    def meili_geo(self) -> MeiliGeo:
        """
        Return geographic coordinates for geo-search.

        Override this method when supports_geo=True in MeiliMeta.

        Example:
            def meili_geo(self) -> MeiliGeo:
                return {"lat": self.latitude, "lng": self.longitude}
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} has supports_geo=True but doesn't implement meili_geo()"
        )
