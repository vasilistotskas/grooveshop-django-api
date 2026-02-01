from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, NamedTuple

from django.db.models import Case, When

from meili._client import client

if TYPE_CHECKING:
    from .models import IndexMixin


class Radius(NamedTuple):
    lat: float | str
    lng: float | str
    radius: int


class BoundingBox(NamedTuple):
    top_right: tuple[float | str, float | str]
    bottom_left: tuple[float | str, float | str]


class Point(NamedTuple):
    lat: float | str
    lng: float | str


@dataclass(frozen=True)
class QueryState:
    """Immutable state container for IndexQuerySet to enable proper chaining."""

    offset: int = 0
    limit: int = 20
    filters: tuple[str, ...] = ()
    facets: tuple[str, ...] = ()
    attributes_to_retrieve: tuple[str, ...] = ("*",)
    attributes_to_crop: tuple[str, ...] = ()
    crop_length: int = 10
    crop_marker: str = "..."
    attributes_to_highlight: tuple[str, ...] = ("*",)
    highlight_pre_tag: str = "<mark>"
    highlight_post_tag: str = "</mark>"
    show_matches_position: bool = True
    sort: tuple[str, ...] = ()
    matching_strategy: Literal["last", "all"] = "last"
    show_ranking_score: bool = True
    attributes_to_search_on: tuple[str, ...] = ("*",)
    locales: tuple[str, ...] = ()

    def with_updates(self, **kwargs) -> "QueryState":
        """Create a new QueryState with updated values."""
        current = {
            "offset": self.offset,
            "limit": self.limit,
            "filters": self.filters,
            "facets": self.facets,
            "attributes_to_retrieve": self.attributes_to_retrieve,
            "attributes_to_crop": self.attributes_to_crop,
            "crop_length": self.crop_length,
            "crop_marker": self.crop_marker,
            "attributes_to_highlight": self.attributes_to_highlight,
            "highlight_pre_tag": self.highlight_pre_tag,
            "highlight_post_tag": self.highlight_post_tag,
            "show_matches_position": self.show_matches_position,
            "sort": self.sort,
            "matching_strategy": self.matching_strategy,
            "show_ranking_score": self.show_ranking_score,
            "attributes_to_search_on": self.attributes_to_search_on,
            "locales": self.locales,
        }
        current.update(kwargs)
        return QueryState(**current)


class IndexQuerySet:
    """
    Immutable queryset-like interface for Meilisearch queries.

    Each method returns a new IndexQuerySet instance, allowing safe chaining
    without state mutation issues.
    """

    def __init__(
        self, model: type["IndexMixin"], state: QueryState | None = None
    ):
        self._model = model
        self._state = state or QueryState()
        # Initialize mutable state for method chaining
        self.__offset = 0
        self.__limit = 20
        self.__filters = []
        self.__facets = []
        self.__attributes_to_retrieve = ["*"]
        self.__attributes_to_crop = []
        self.__crop_length = 10
        self.__crop_marker = "..."
        self.__attributes_to_highlight = ["*"]
        self.__highlight_pre_tag = "<mark>"
        self.__highlight_post_tag = "</mark>"
        self.__show_matches_position = True
        self.__sort = []
        self.__matching_strategy = "last"
        self.__show_ranking_score = True
        self.__attributes_to_search_on = ["*"]
        self.__locales = []

    @property
    def model(self) -> type["IndexMixin"]:
        return self._model

    @property
    def index(self):
        return client.get_index(self._model._meilisearch["index_name"])

    def __repr__(self):
        return f"<IndexQuerySet for {self.model.__name__}>"

    def __str__(self):
        return f"IndexQuerySet for {self.model.__name__}"

    def __getitem__(self, index):
        if isinstance(index, slice):
            self.__offset = index.start
            self.__limit = index.stop
            return self
        else:
            raise TypeError("IndexQuerySet indices must be slices")

    def count(self) -> int:
        return self.index.get_stats().number_of_documents

    def paginate(self, limit: int, offset: int):
        self.__limit = limit
        self.__offset = offset
        return self

    def order_by(self, *fields: str):
        # Clear existing sort to avoid accumulation
        self.__sort = []
        for field in fields:
            geopoint = "_" if "geoPoint" in field else ""
            if field.startswith("-"):
                self.__sort.append(f"{geopoint}{field[1:]}:desc")
            else:
                self.__sort.append(f"{geopoint}{field}:asc")
        return self

    def filter(self, *geo_filters, **filters):
        self._apply_geo_filters(*geo_filters)
        self._apply_regular_filters(**filters)
        return self

    def _apply_geo_filters(self, *geo_filters):
        for geo_filter in geo_filters:
            if not self.model._meilisearch["supports_geo"]:
                raise TypeError(
                    f"Model {self.model.__name__} does not support geo filters"
                )
            if not isinstance(geo_filter, Radius | BoundingBox):
                raise TypeError(
                    f"Unnamed Argument must be of type Radius or BoundingBox, not {type(geo_filter)}"
                )
            if isinstance(geo_filter, Radius):
                self.__filters.append(
                    f"_geoRadius({geo_filter.lat}, {geo_filter.lng}, {geo_filter.radius})"
                )
            elif isinstance(geo_filter, BoundingBox):
                self.__filters.append(
                    f"_geoBoundingBox([{geo_filter.top_right[0]}, {geo_filter.top_right[1]}], [{geo_filter.bottom_left[0]}, {geo_filter.bottom_left[1]}])"
                )
        return self

    def _apply_regular_filters(self, **filters):
        """
        Apply filters to the search query.

        Supported lookups:
        - __exact or no lookup: Exact match
        - __gte, __gt, __lte, __lt: Numeric comparisons
        - __in: Value in list
        - __range: Value in range
        - __exists: Field existence
        - __isnull: Null checks
        - __contains: Substring matching (requires experimental feature)

        Example:
            .filter(name__contains="laptop")
            # Generates: name CONTAINS "laptop"

        Note: CONTAINS operator is experimental and must be enabled via:
            POST /experimental-features
            {"containsFilter": true}
        """
        for full_lookup, value in filters.items():
            if "__" not in full_lookup or "__exact" in full_lookup:
                if (
                    value == ""
                    or (isinstance(value, list) and len(value) == 0)
                    or value == {}
                ):
                    self.__filters.append(
                        f"{full_lookup.split('__')[0]} IS EMPTY"
                    )
                elif value is None:
                    self.__filters.append(
                        f"{full_lookup.split('__')[0]} IS NULL"
                    )
                else:
                    self.__filters.append(
                        f"{full_lookup.split('__')[0]} = '{value}'"
                        if isinstance(value, str)
                        else f"{full_lookup.split('__')[0]} = {value}"
                    )
            elif "__gte" in full_lookup:
                if not isinstance(value, int | float):
                    raise TypeError(
                        f"Cannot compare {type(value)} with int or float"
                    )
                self.__filters.append(
                    f"{full_lookup.split('__')[0]} >= {value}"
                )
            elif "__gt" in full_lookup:
                if not isinstance(value, int | float):
                    raise TypeError(
                        f"Cannot compare {type(value)} with int or float"
                    )
                self.__filters.append(f"{full_lookup.split('__')[0]} > {value}")
            elif "__lte" in full_lookup:
                if not isinstance(value, int | float):
                    raise TypeError(
                        f"Cannot compare {type(value)} with int or float"
                    )
                self.__filters.append(
                    f"{full_lookup.split('__')[0]} <= {value}"
                )
            elif "__lt" in full_lookup:
                if not isinstance(value, int | float):
                    raise TypeError(
                        f"Cannot compare {type(value)} with int or float"
                    )
                self.__filters.append(f"{full_lookup.split('__')[0]} < {value}")
            elif "__in" in full_lookup:
                if not isinstance(value, list):
                    raise TypeError(f"Cannot compare {type(value)} with list")
                self.__filters.append(
                    f"{full_lookup.split('__')[0]} IN {value}"
                )
            elif "__range" in full_lookup:
                if not isinstance(value, range | list | tuple):
                    raise TypeError(
                        f"Cannot compare {type(value)} with range, list or tuple"
                    )
                self.__filters.append(
                    f"{full_lookup.split('__')[0]} {value[0]} TO {value[1]}"
                    if not isinstance(value, range)
                    else f"{full_lookup.split('__')[0]} {value.start} TO {value.stop}"
                )
            elif "__exists" in full_lookup:
                if not isinstance(value, bool):
                    raise TypeError(f"Cannot compare {type(value)} with bool")
                self.__filters.append(
                    f"{full_lookup.split('__')[0]} {'NOT ' if not value else ''}EXISTS"
                )
            elif "__isnull" in full_lookup:
                if not isinstance(value, bool):
                    raise TypeError(f"Cannot compare {type(value)} with bool")
                self.__filters.append(
                    f"{full_lookup.split('__')[0]} {'NOT ' if not value else ''}IS NULL"
                )
            elif "__contains" in full_lookup:
                if not isinstance(value, str):
                    raise TypeError(
                        f"CONTAINS operator only supports string values, not {type(value).__name__}. "
                        f"Ensure the field '{full_lookup.split('__')[0]}' is a string field and the value is a string."
                    )
                field_name = full_lookup.split("__")[0]
                self.__filters.append(f'{field_name} CONTAINS "{value}"')

    def matching_strategy(self, strategy: Literal["last", "all"]):
        self.__matching_strategy = strategy
        return self

    def attributes_to_search_on(self, *attributes):
        self.__attributes_to_search_on.append(*attributes)
        return self

    def locales(self, *locales: str):
        self.__locales = list(locales)
        return self

    def facets(self, *facet_fields: str):
        """
        Add facets to the search query for dynamic filter counts and statistics.

        Args:
            *facet_fields: Field names to compute facets for

        Returns:
            self for method chaining
        """
        self.__facets = list(facet_fields)
        return self

    def search(self, q: str = ""):
        results = self.index.search(
            q,
            {
                "filter": self.__filters,
                "facets": self.__facets,
                "offset": self.__offset,
                "limit": self.__limit,
                "attributesToRetrieve": self.__attributes_to_retrieve,
                "attributesToCrop": self.__attributes_to_crop,
                "cropLength": self.__crop_length,
                "cropMarker": self.__crop_marker,
                "attributesToHighlight": self.__attributes_to_highlight,
                "highlightPreTag": self.__highlight_pre_tag,
                "highlightPostTag": self.__highlight_post_tag,
                "showMatchesPosition": self.__show_matches_position,
                "sort": self.__sort,
                "matchingStrategy": self.__matching_strategy,
                "showRankingScore": self.__show_ranking_score,
                "attributesToSearchOn": self.__attributes_to_search_on,
                "locales": self.__locales,
            },
        )

        id_field = getattr(self.model.MeiliMeta, "primary_key", "id")
        hits = results.get("hits", [])
        pk_list = [hit[id_field] for hit in hits]
        preserved_order = Case(
            *[When(pk=pk, then=pos) for pos, pk in enumerate(pk_list)]
        )
        filtered_objects = self.model.objects.filter(pk__in=pk_list).order_by(
            preserved_order
        )

        enriched_results = []
        for obj in filtered_objects:
            hit = next(hit for hit in hits if str(hit["id"]) == str(obj.pk))
            enriched_results.append(
                {
                    "object": obj,
                    "_formatted": hit.get("_formatted", {}),
                    "_matchesPosition": hit.get("_matchesPosition", {}),
                    "_rankingScore": hit.get("_rankingScore", None),
                }
            )

        response_data = {
            "results": enriched_results,
            "estimated_total_hits": results["estimatedTotalHits"],
            "offset": self.__offset,
            "limit": self.__limit,
        }

        # Add facet data if present
        if "facetDistribution" in results:
            response_data["facetDistribution"] = results["facetDistribution"]
        if "facetStats" in results:
            response_data["facetStats"] = results["facetStats"]

        return response_data
