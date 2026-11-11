"""
Data classes for Meilisearch configuration.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MeiliIndexSettings:
    """
    Immutable configuration for a Meilisearch index.

    Maps to Meilisearch's index settings API.
    See: https://www.meilisearch.com/docs/reference/api/settings
    """

    displayed_fields: list[str] | None = None
    searchable_fields: list[str] | None = None
    filterable_fields: list[str] | None = None
    range_fields: list[str] | None = None
    facet_search: bool = False
    sortable_fields: list[str] | None = None
    ranking_rules: list[str] | None = None
    stop_words: list[str] | None = None
    synonyms: dict[str, list[str]] | None = None
    distinct_attribute: str | None = None
    typo_tolerance: dict[str, Any] | None = None
    faceting: dict[str, Any] | None = None
    pagination: dict[str, Any] | None = None
    search_cutoff_ms: int | None = None
    #: The document attribute the engine's primary key is. Always
    #: filterable (equality only, no facet search): a delete by filter
    #: needs it, and the SDK deprecates deleting by ids.
    primary_key: str | None = None

    def filterable_attributes_payload(self) -> list[dict[str, Any]]:
        """The granular ``filterableAttributes`` the engine is sent.

        Every filterable field gets equality (``=``, ``IN``, ``EXISTS``,
        and the facet counts); only ``range_fields`` also get comparison
        (``<``, ``>=``, ``TO``, and ``facetStats``). A field the engine
        indexes for an operator nobody uses costs indexing time and disk,
        and the facet-search database costs the same again.

        ``primary_key`` is added last as equality only with no facet
        search, unless it is already listed: the cheapest way to make a
        delete by filter possible.

        See https://www.meilisearch.com/docs/learn/filtering_and_sorting/configure_granular_filters
        """
        filterable = list(self.filterable_fields or [])
        range_fields = self.range_fields or []
        unknown = set(range_fields) - set(filterable)
        if unknown:
            raise ValueError(
                f"range_fields {sorted(unknown)} are not in filterable_fields"
            )

        payload = []
        plain = [f for f in filterable if f not in range_fields]
        listed = {*range_fields, *plain}
        pk_only = (
            [self.primary_key]
            if self.primary_key and self.primary_key not in listed
            else []
        )
        for names, comparison, facet_search in (
            (range_fields, True, self.facet_search),
            (plain, False, self.facet_search),
            (pk_only, False, False),
        ):
            if names:
                payload.append(
                    {
                        "attributePatterns": names,
                        "features": {
                            "facetSearch": facet_search,
                            "filter": {
                                "equality": True,
                                "comparison": comparison,
                            },
                        },
                    }
                )
        return payload
