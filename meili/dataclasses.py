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

    def filterable_attributes_payload(self) -> list[dict[str, Any]]:
        """The granular ``filterableAttributes`` the engine is sent.

        Every filterable field gets equality (``=``, ``IN``, ``EXISTS``,
        and the facet counts); only ``range_fields`` also get comparison
        (``<``, ``>=``, ``TO``, and ``facetStats``). A field the engine
        indexes for an operator nobody uses costs indexing time and disk,
        and the facet-search database costs the same again.

        See https://www.meilisearch.com/docs/learn/filtering_and_sorting/configure_granular_filters
        """
        filterable = self.filterable_fields or []
        range_fields = self.range_fields or []
        unknown = set(range_fields) - set(filterable)
        if unknown:
            raise ValueError(
                f"range_fields {sorted(unknown)} are not in filterable_fields"
            )

        payload = []
        for names, comparison in (
            (range_fields, True),
            ([f for f in filterable if f not in range_fields], False),
        ):
            if names:
                payload.append(
                    {
                        "attributePatterns": names,
                        "features": {
                            "facetSearch": self.facet_search,
                            "filter": {
                                "equality": True,
                                "comparison": comparison,
                            },
                        },
                    }
                )
        return payload
