from typing import Iterable
from typing import TypedDict

from django.conf import settings
from django.db import models
from meilisearch.models.task import TaskInfo

from meili._client import client as _client
from meili.querysets import IndexQuerySet


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
    tasks: list[TaskInfo]


class IndexMixin(models.Model):
    meilisearch: IndexQuerySet
    _meilisearch: _Meili

    class MeiliMeta:
        displayed_fields: Iterable[str] = None
        searchable_fields: Iterable[str] = None
        filterable_fields: Iterable[str] = None
        sortable_fields: Iterable[str] = None
        ranking_rules: Iterable[str] = None
        stop_words: Iterable[str] = None
        synonyms: dict[str, list[str]] = None
        distinct_attribute: str = None
        typo_tolerance: dict[str, any] = None
        faceting: dict[str, any] = None
        pagination: dict[str, any] = None
        supports_geo: bool = False
        index_name: str = None
        primary_key: str = "pk"

    def __init_subclass__(cls) -> None:
        index_name = getattr(cls.MeiliMeta, "index_name", cls.__name__)
        primary_key = getattr(cls.MeiliMeta, "primary_key", "pk")
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

        if settings.MEILISEARCH.get("OFFLINE", False):
            cls._meilisearch = _Meili(
                primary_key=primary_key,
                index_name=index_name,
                displayed_fields=displayed_fields,
                searchable_fields=searchable_fields,
                filterable_fields=filterable_fields,
                sortable_fields=sortable_fields,
                supports_geo=supports_geo,
                tasks=[],
            )
        else:
            _client.create_index(index_name, primary_key).with_settings(
                index_name=index_name,
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

        cls._meilisearch = _Meili(
            primary_key=primary_key,
            index_name=index_name,
            displayed_fields=displayed_fields,
            searchable_fields=searchable_fields,
            filterable_fields=filterable_fields,
            sortable_fields=sortable_fields,
            supports_geo=supports_geo,
            tasks=[task for task in _client.tasks],
        )
        _client.flush_tasks()

        cls.meilisearch = IndexQuerySet(cls)

    def meili_filter(self) -> bool:  # noqa
        return True

    @classmethod
    def get_additional_meili_fields(cls) -> dict:
        return {}

    def meili_serialize(self):
        from json import loads
        from django.core.serializers import serialize

        serialized_model = loads(
            serialize(
                "json",
                [self],
                use_natural_foreign_keys=True,
                use_natural_primary_keys=True,
            )
        )[0]

        data = serialized_model["fields"]

        additional_fields = self.get_additional_meili_fields()
        for field_name, value_getter in additional_fields.items():
            try:
                data[field_name] = value_getter(self)
            except AttributeError:
                data[field_name] = None

        return data

    def meili_geo(self) -> MeiliGeo:
        raise ValueError("Model does not support geolocation")

    class Meta:
        abstract = True
