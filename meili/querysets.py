"""
Meilisearch QuerySet implementation.

Provides a Django ORM-like interface for building and executing Meilisearch queries
with support for filtering, sorting, pagination, facets, and geo-search.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, NamedTuple, Self

from django.db.models import Case, When

from meili._client import client

if TYPE_CHECKING:
    from django.db.models import Model


class Radius(NamedTuple):
    """Geo filter for circular area search."""

    lat: float | str
    lng: float | str
    radius: int


class BoundingBox(NamedTuple):
    """Geo filter for rectangular area search."""

    top_right: tuple[float | str, float | str]
    bottom_left: tuple[float | str, float | str]


class Point(NamedTuple):
    """Geographic point coordinates."""

    lat: float | str
    lng: float | str


@dataclass(slots=True)
class QueryState:
    """
    Mutable state container for IndexQuerySet.

    Using a separate state class keeps the queryset interface clean
    while allowing efficient state management.
    """

    offset: int = 0
    limit: int = 20
    filters: list[str] = field(default_factory=list)
    facets: list[str] = field(default_factory=list)
    attributes_to_retrieve: list[str] = field(default_factory=lambda: ["*"])
    attributes_to_crop: list[str] = field(default_factory=list)
    crop_length: int = 10
    crop_marker: str = "..."
    attributes_to_highlight: list[str] = field(default_factory=lambda: ["*"])
    highlight_pre_tag: str = "<mark>"
    highlight_post_tag: str = "</mark>"
    show_matches_position: bool = True
    sort: list[str] = field(default_factory=list)
    matching_strategy: Literal["last", "all", "frequency"] = "last"
    show_ranking_score: bool = True
    attributes_to_search_on: list[str] = field(default_factory=lambda: ["*"])
    locales: list[str] = field(default_factory=list)


class IndexQuerySet[T: Model]:
    """
    QuerySet-like interface for Meilisearch queries.

    Provides a Django ORM-like API for building and executing Meilisearch queries.
    Methods return self for chaining.

    Example:
        results = (
            Product.meilisearch
            .filter(category="electronics", price__gte=100)
            .order_by("-popularity")
            .paginate(limit=20, offset=0)
            .search("laptop")
        )

    Type Parameters:
        T: The Django model type this queryset operates on
    """

    __slots__ = ("_model", "_state")

    def __init__(self, model: type[T], state: QueryState | None = None) -> None:
        self._model = model
        self._state = state or QueryState()

    @property
    def model(self) -> type[T]:
        """Return the model class."""
        return self._model

    @property
    def index(self):
        """Return the Meilisearch index for this model."""
        return client.get_index(self._model._meilisearch["index_name"])

    @property
    def filters(self) -> list[str]:
        """Return current filters."""
        return self._state.filters

    @property
    def facets(self) -> list[str]:
        """Return current facets."""
        return self._state.facets

    @property
    def sort(self) -> list[str]:
        """Return current sort order."""
        return self._state.sort

    def __repr__(self) -> str:
        return f"<IndexQuerySet for {self.model.__name__}>"

    def __str__(self) -> str:
        return f"IndexQuerySet for {self.model.__name__}"

    def __getitem__(self, index) -> Self:
        if isinstance(index, slice):
            self._state.offset = index.start or 0
            self._state.limit = index.stop or 20
            return self
        raise TypeError("IndexQuerySet indices must be slices")

    def count(self) -> int:
        """Return the total number of documents in the index."""
        return self.index.get_stats().number_of_documents

    def paginate(self, limit: int, offset: int = 0) -> Self:
        """Set pagination parameters."""
        self._state.limit = limit
        self._state.offset = offset
        return self

    def order_by(self, *fields: str) -> Self:
        """
        Set sort order for results.

        Prefix field with '-' for descending order.
        Supports geo sorting with geoPoint fields.

        Example:
            .order_by("-popularity", "price")
        """
        self._state.sort.clear()
        for f in fields:
            geopoint = "_" if "geoPoint" in f else ""
            if f.startswith("-"):
                self._state.sort.append(f"{geopoint}{f[1:]}:desc")
            else:
                self._state.sort.append(f"{geopoint}{f}:asc")
        return self

    def filter(self, *geo_filters: Radius | BoundingBox, **filters) -> Self:
        """
        Apply filters to the search query.

        Supports geo filters (Radius, BoundingBox) and field filters.

        Supported lookups:
        - __exact or no lookup: Exact match
        - __gte, __gt, __lte, __lt: Numeric comparisons
        - __in: Value in list
        - __range: Value in range (tuple or range object)
        - __exists: Field existence check
        - __isnull: Null checks
        - __contains: Substring matching (experimental feature)

        Example:
            .filter(Radius(lat=48.8, lng=2.3, radius=1000))
            .filter(category="electronics", price__gte=100, price__lte=500)
        """
        self._apply_geo_filters(*geo_filters)
        self._apply_field_filters(**filters)
        return self

    def _apply_geo_filters(self, *geo_filters: Radius | BoundingBox) -> None:
        """Build geo filter expressions."""
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
                self._state.filters.append(
                    f"_geoRadius({geo_filter.lat}, {geo_filter.lng}, {geo_filter.radius})"
                )
            elif isinstance(geo_filter, BoundingBox):
                self._state.filters.append(
                    f"_geoBoundingBox([{geo_filter.top_right[0]}, {geo_filter.top_right[1]}], "
                    f"[{geo_filter.bottom_left[0]}, {geo_filter.bottom_left[1]}])"
                )

    def _apply_field_filters(self, **filters) -> None:
        """
        Apply field filters to the search query.

        Supported lookups:
        - __exact or no lookup: Exact match
        - __gte, __gt, __lte, __lt: Numeric comparisons
        - __in: Value in list
        - __range: Value in range
        - __exists: Field existence
        - __isnull: Null checks
        - __contains: Substring matching (requires experimental feature)
        """
        for lookup, value in filters.items():
            field_name = lookup.split("__")[0]

            if "__" not in lookup or "__exact" in lookup:
                self._add_exact_filter(field_name, value)
            elif "__gte" in lookup:
                self._add_comparison_filter(field_name, ">=", value)
            elif "__gt" in lookup:
                self._add_comparison_filter(field_name, ">", value)
            elif "__lte" in lookup:
                self._add_comparison_filter(field_name, "<=", value)
            elif "__lt" in lookup:
                self._add_comparison_filter(field_name, "<", value)
            elif "__in" in lookup:
                self._add_in_filter(field_name, value)
            elif "__range" in lookup:
                self._add_range_filter(field_name, value)
            elif "__exists" in lookup:
                self._add_exists_filter(field_name, value)
            elif "__isnull" in lookup:
                self._add_isnull_filter(field_name, value)
            elif "__contains" in lookup:
                self._add_contains_filter(field_name, value)

    def _add_exact_filter(self, field_name: str, value) -> None:
        """Add exact match filter."""
        if (
            value == ""
            or (isinstance(value, list) and len(value) == 0)
            or value == {}
        ):
            self._state.filters.append(f"{field_name} IS EMPTY")
        elif value is None:
            self._state.filters.append(f"{field_name} IS NULL")
        elif isinstance(value, str):
            self._state.filters.append(f"{field_name} = '{value}'")
        else:
            self._state.filters.append(f"{field_name} = {value}")

    def _add_comparison_filter(
        self, field_name: str, operator: str, value
    ) -> None:
        """Add numeric comparison filter."""
        if not isinstance(value, int | float):
            raise TypeError(f"Cannot compare {type(value)} with int or float")
        self._state.filters.append(f"{field_name} {operator} {value}")

    def _add_in_filter(self, field_name: str, value) -> None:
        """Add IN filter."""
        if not isinstance(value, list):
            raise TypeError(f"Cannot compare {type(value)} with list")
        self._state.filters.append(f"{field_name} IN {value}")

    def _add_range_filter(self, field_name: str, value) -> None:
        """Add range filter."""
        if not isinstance(value, range | list | tuple):
            raise TypeError(
                f"Cannot compare {type(value)} with range, list or tuple"
            )
        if isinstance(value, range):
            self._state.filters.append(
                f"{field_name} {value.start} TO {value.stop}"
            )
        else:
            self._state.filters.append(f"{field_name} {value[0]} TO {value[1]}")

    def _add_exists_filter(self, field_name: str, value) -> None:
        """Add EXISTS filter."""
        if not isinstance(value, bool):
            raise TypeError(f"Cannot compare {type(value)} with bool")
        prefix = "" if value else "NOT "
        self._state.filters.append(f"{field_name} {prefix}EXISTS")

    def _add_isnull_filter(self, field_name: str, value) -> None:
        """Add IS NULL filter."""
        if not isinstance(value, bool):
            raise TypeError(f"Cannot compare {type(value)} with bool")
        prefix = "" if value else "NOT "
        self._state.filters.append(f"{field_name} {prefix}IS NULL")

    def _add_contains_filter(self, field_name: str, value) -> None:
        """Add CONTAINS filter (experimental Meilisearch feature)."""
        if not isinstance(value, str):
            raise TypeError(
                f"CONTAINS operator only supports string values, not {type(value).__name__}. "
                f"Ensure the field '{field_name}' is a string field and the value is a string."
            )
        self._state.filters.append(f'{field_name} CONTAINS "{value}"')

    def matching_strategy(
        self, strategy: Literal["last", "all", "frequency"]
    ) -> Self:
        """
        Set the matching strategy for search.

        - "last": Only last word must match (default, best for autocomplete)
        - "all": All words must match (stricter)
        - "frequency": Prioritize documents with more matching words
        """
        self._state.matching_strategy = strategy
        return self

    def attributes_to_search_on(self, *attributes: str) -> Self:
        """Limit search to specific attributes."""
        self._state.attributes_to_search_on.append(*attributes)
        return self

    def locales(self, *locales: str) -> Self:
        """Set locales for language-specific search."""
        self._state.locales = list(locales)
        return self

    def set_facets(self, *facet_fields: str) -> Self:
        """
        Add facets to the search query for dynamic filter counts and statistics.

        Returns facetDistribution and facetStats in search results.

        Example:
            .set_facets("category", "brand", "price")
        """
        self._state.facets = list(facet_fields)
        return self

    def highlight(
        self,
        *attributes: str,
        pre_tag: str = "<mark>",
        post_tag: str = "</mark>",
    ) -> Self:
        """Configure highlighting for search results."""
        self._state.attributes_to_highlight = (
            list(attributes) if attributes else ["*"]
        )
        self._state.highlight_pre_tag = pre_tag
        self._state.highlight_post_tag = post_tag
        return self

    def crop(
        self,
        *attributes: str,
        length: int = 10,
        marker: str = "...",
    ) -> Self:
        """Configure cropping for long text fields."""
        self._state.attributes_to_crop = list(attributes)
        self._state.crop_length = length
        self._state.crop_marker = marker
        return self

    def only(self, *attributes: str) -> Self:
        """Limit which attributes are returned in results."""
        self._state.attributes_to_retrieve = list(attributes)
        return self

    def search(self, q: str = "") -> dict:
        """
        Execute the search query and return enriched results.

        Returns a dictionary with:
        - results: List of dicts with 'object', '_formatted', '_matchesPosition', '_rankingScore'
        - estimated_total_hits: Total matching documents
        - offset: Current offset
        - limit: Current limit
        - facetDistribution: Facet counts (if facets requested)
        - facetStats: Facet statistics (if facets requested)
        """
        search_params = self._build_search_params()
        results = self.index.search(q, search_params)
        enriched_results = self._enrich_results(results)

        response_data = {
            "results": enriched_results,
            "estimated_total_hits": results.get("estimatedTotalHits", 0),
            "offset": self._state.offset,
            "limit": self._state.limit,
        }

        if "facetDistribution" in results:
            response_data["facetDistribution"] = results["facetDistribution"]
        if "facetStats" in results:
            response_data["facetStats"] = results["facetStats"]

        return response_data

    def raw_search(self, q: str = "") -> dict:
        """
        Execute search and return raw Meilisearch results without ORM enrichment.

        Useful for performance-critical scenarios or when you don't need Django objects.
        """
        search_params = {
            "filter": self._state.filters,
            "facets": self._state.facets,
            "offset": self._state.offset,
            "limit": self._state.limit,
            "attributesToRetrieve": self._state.attributes_to_retrieve,
            "sort": self._state.sort,
            "matchingStrategy": self._state.matching_strategy,
        }

        if self._state.locales:
            search_params["locales"] = self._state.locales

        return self.index.search(q, search_params)

    def _build_search_params(self) -> dict:
        """Build the search parameters dictionary."""
        return {
            "filter": self._state.filters,
            "facets": self._state.facets,
            "offset": self._state.offset,
            "limit": self._state.limit,
            "attributesToRetrieve": self._state.attributes_to_retrieve,
            "attributesToCrop": self._state.attributes_to_crop,
            "cropLength": self._state.crop_length,
            "cropMarker": self._state.crop_marker,
            "attributesToHighlight": self._state.attributes_to_highlight,
            "highlightPreTag": self._state.highlight_pre_tag,
            "highlightPostTag": self._state.highlight_post_tag,
            "showMatchesPosition": self._state.show_matches_position,
            "sort": self._state.sort,
            "matchingStrategy": self._state.matching_strategy,
            "showRankingScore": self._state.show_ranking_score,
            "attributesToSearchOn": self._state.attributes_to_search_on,
            "locales": self._state.locales,
        }

    def _enrich_results(self, results: dict) -> list[dict]:
        """Enrich Meilisearch results with Django ORM objects."""
        id_field = getattr(self.model.MeiliMeta, "primary_key", "id")
        hits = results.get("hits", [])

        if not hits:
            return []

        pk_list = [hit[id_field] for hit in hits]

        # Preserve Meilisearch ranking order
        preserved_order = Case(
            *[When(pk=pk, then=pos) for pos, pk in enumerate(pk_list)]
        )

        # Fetch Django objects
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
                    "_rankingScore": hit.get("_rankingScore"),
                }
            )

        return enriched_results
