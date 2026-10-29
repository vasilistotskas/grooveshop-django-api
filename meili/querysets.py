"""
Meilisearch QuerySet implementation.

Provides a Django ORM-like interface for building and executing Meilisearch queries
with support for filtering, sorting, pagination, facets, and geo-search.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, NamedTuple, Self

from meili._client import client

if TYPE_CHECKING:
    from collections.abc import Iterable

    from meili.models import IndexMixin


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


# The markup every search endpoint wraps a matched term in.
HIGHLIGHT_PRE_TAG = "<mark>"
HIGHLIGHT_POST_TAG = "</mark>"


@dataclass(frozen=True, slots=True)
class FacetGroup:
    """One multi-select group inside a facet field.

    A facet field can hold the keys of several groups side by side: the
    product index's ``attribute_values`` lists the values of every
    attribute. Standard faceted search counts a group's values without
    that group's own selection, so picking one value does not hide its
    alternatives, while every other group's selection still narrows them.

    ``clause`` is the filter the group contributed, ``selected`` the facet
    keys that clause asks for and ``members`` every facet key the group
    governs, selected or not (all as strings, the way the engine reports
    them). ``members`` is ``None`` when the group governs the whole facet
    field, as the category and brand filters do: every key of the count
    query is then the group's own.
    """

    facet: str
    clause: str
    selected: frozenset[str]
    members: frozenset[str] | None


@dataclass(frozen=True, slots=True)
class StatsBaseline:
    """Facet statistics taken over a fixed set of documents.

    A price slider or a popularity range describes the catalogue, not the
    shopper's current filters, so its ``facetStats`` come from a query of
    its own: no search text and only the ``filters`` of the baseline.
    """

    facets: tuple[str, ...]
    filters: tuple[str, ...]
    locales: tuple[str, ...]


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
    facet_groups: list[FacetGroup] = field(default_factory=list)
    stats_baselines: list[StatsBaseline] = field(default_factory=list)
    attributes_to_retrieve: list[str] = field(default_factory=lambda: ["*"])
    attributes_to_crop: list[str] = field(default_factory=list)
    crop_length: int = 10
    crop_marker: str = "..."
    attributes_to_highlight: list[str] = field(default_factory=lambda: ["*"])
    highlight_pre_tag: str = HIGHLIGHT_PRE_TAG
    highlight_post_tag: str = HIGHLIGHT_POST_TAG
    show_matches_position: bool = True
    sort: list[str] = field(default_factory=list)
    matching_strategy: Literal["last", "all", "frequency"] = "last"
    show_ranking_score: bool = True
    attributes_to_search_on: list[str] = field(default_factory=lambda: ["*"])
    locales: list[str] = field(default_factory=list)


class IndexQuerySet[T: IndexMixin]:
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
        """Master-key index handle for admin/stats operations."""
        return client.get_index(self._model.get_meili_index_name())

    @property
    def search_index(self):
        """Read-only index handle for public search queries."""
        return client.get_search_index(self._model.get_meili_index_name())

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

    def filter_facet_group(
        self,
        facet: str,
        selected: Iterable[int | str],
        members: Iterable[int | str] | None,
    ) -> Self:
        """
        Keep documents whose ``facet`` holds ANY of ``selected``, as one
        multi-select group of that facet.

        Groups are ANDed with each other and with every other filter, so
        two groups on the same facet are "any of these AND any of those".
        When ``facet`` is also requested through ``set_facets``, ``search``
        counts each group's ``members`` without that group's own clause
        (see ``FacetGroup``); ``members=None`` makes the group the whole
        facet field.

        Example:
            .filter_facet_group("attribute_values", [10, 11], members=[10, 11, 12])
            .filter_facet_group("attribute_values", [20], members=[20, 21])
        """
        selected = list(selected)
        self._add_in_filter(facet, selected)
        self._state.facet_groups.append(
            FacetGroup(
                facet=facet,
                clause=self._state.filters[-1],
                selected=frozenset(str(key) for key in selected),
                members=(
                    None
                    if members is None
                    else frozenset(str(member) for member in members)
                ),
            )
        )
        return self

    def stats_over(self, baseline: IndexQuerySet, *facet_fields: str) -> Self:
        """
        Take the ``facetStats`` of ``facet_fields`` over the documents
        ``baseline`` filters to, with no search text, instead of over this
        queryset's own matches (see ``StatsBaseline``). The fields must
        not also be in ``set_facets``, or the baseline's stats replace the
        search's own.

        Example:
            .stats_over(Model.meilisearch.filter(active=True), "price")
        """
        self._state.stats_baselines.append(
            StatsBaseline(
                facets=facet_fields,
                filters=tuple(baseline.filters),
                locales=tuple(baseline._state.locales),
            )
        )
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
            escaped = value.replace("\\", "\\\\").replace("'", "\\'")
            self._state.filters.append(f"{field_name} = '{escaped}'")
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
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        self._state.filters.append(f'{field_name} CONTAINS "{escaped}"')

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
        self._state.attributes_to_search_on.extend(attributes)
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
        counted_groups = [
            group
            for group in self._state.facet_groups
            if group.facet in self._state.facets
        ]
        if counted_groups or self._state.stats_baselines:
            results = self._search_with_group_counts(
                q, search_params, counted_groups
            )
        else:
            results = self.search_index.search(q, search_params)
        enriched_results = self._enrich_results(results)

        response_data = {
            "results": enriched_results,
            "estimated_total_hits": results.get("estimatedTotalHits", 0),
            "offset": self._state.offset,
            "limit": self._state.limit,
            # The engine's own timing, forwarded for search analytics.
            # This dict is INTERNAL — views pick the fields they expose —
            # so carrying it here costs nothing at the API surface. It
            # used to be dropped, which left every ``SearchQuery`` row
            # with a NULL ``processing_time_ms`` and pinned the analytics
            # average at 0.0.
            "processing_time_ms": results.get("processingTimeMs"),
        }

        if "facetDistribution" in results:
            response_data["facetDistribution"] = results["facetDistribution"]
        if "facetStats" in results:
            response_data["facetStats"] = results["facetStats"]

        return response_data

    def _search_with_group_counts(
        self, q: str, params: dict, groups: list[FacetGroup]
    ) -> dict:
        """
        Run the search plus one count-only query per facet group and one
        per stats baseline, in one ``multi-search`` request (Meilisearch's
        documented recipe for disjunctive facets).

        Each group query repeats ``params`` (the same ``q``, matching
        strategy and locales, so it matches the same documents) with the
        group's own clause left out, ``limit`` 0 and only its facet.

        Only the group's unselected siblings are read from that query: the
        main query has them narrowed by the group's own selection. A
        selected value keeps the main query's count, which is already its
        disjunctive count (a document holding it matches the group's
        clause either way), and taking it from there keeps it listed when
        the count query, which matches more documents, pushes it past the
        engine's ``maxValuesPerFacet`` cap. A sibling the cap omits is
        simply not listed, the truncation every facet has. A group that
        governs the whole facet field (``members`` is ``None``) lists what
        its count query reports, which is what a request without the group's
        clause would have returned.

        A baseline query repeats neither ``q`` nor the search's filters: it
        contributes only ``facetStats``.
        """
        index_uid = self._model.get_meili_index_name()
        queries = [{"indexUid": index_uid, "q": q, **params}]
        queries.extend(
            {
                "indexUid": index_uid,
                "q": q,
                **params,
                "filter": [
                    clause
                    for clause in params["filter"]
                    if clause != group.clause
                ],
                "facets": [group.facet],
                "offset": 0,
                "limit": 0,
            }
            for group in groups
        )
        queries.extend(
            {
                "indexUid": index_uid,
                "q": "",
                "filter": list(baseline.filters),
                "facets": list(baseline.facets),
                "offset": 0,
                "limit": 0,
                "locales": list(baseline.locales),
            }
            for baseline in self._state.stats_baselines
        )
        main, *rest = client.tenant_search_client().multi_search(queries)[
            "results"
        ]
        counts, baselines = rest[: len(groups)], rest[len(groups) :]

        distribution = main.setdefault("facetDistribution", {})
        for group, counted in zip(groups, counts, strict=True):
            own = counted.get("facetDistribution", {}).get(group.facet, {})
            if group.members is None:
                distribution[group.facet] = dict(own)
                continue
            siblings = group.members - group.selected
            distribution[group.facet] = {
                **{
                    key: count
                    for key, count in distribution.get(group.facet, {}).items()
                    if key not in siblings
                },
                **{key: count for key, count in own.items() if key in siblings},
            }
        for baseline in baselines:
            main.setdefault("facetStats", {}).update(
                baseline.get("facetStats", {})
            )
        main["processingTimeMs"] = sum(
            result.get("processingTimeMs", 0) for result in (main, *rest)
        )
        return main

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

        # Meilisearch's ranking order, and each hit by the pk of its object:
        # both looked up per result, so keyed once instead of scanned.
        ranking = {str(pk): position for position, pk in enumerate(pk_list)}
        hits_by_pk = {str(hit["id"]): hit for hit in hits}

        # Hydrate via the model's optimized search queryset when it provides
        # one, so the result serializer doesn't N+1 per hit (e.g. product
        # master counts / main image). Falls back to the plain manager.
        get_search_qs = getattr(self.model, "get_search_result_queryset", None)
        base_qs = (
            get_search_qs() if callable(get_search_qs) else self.model.objects
        )

        # Fetch Django objects, then put them in ranking order here: the
        # database would need a CASE over every pk to do it.
        filtered_objects = sorted(
            base_qs.filter(pk__in=pk_list).order_by(),
            key=lambda obj: ranking[str(obj.pk)],
        )

        enriched_results = []
        for obj in filtered_objects:
            hit = hits_by_pk[str(obj.pk)]
            enriched_results.append(
                {
                    "object": obj,
                    "_formatted": hit.get("_formatted", {}),
                    "_matchesPosition": hit.get("_matchesPosition", {}),
                    "_rankingScore": hit.get("_rankingScore"),
                }
            )

        return enriched_results
