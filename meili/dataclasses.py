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
    sortable_fields: list[str] | None = None
    ranking_rules: list[str] | None = None
    stop_words: list[str] | None = None
    synonyms: dict[str, list[str]] | None = None
    distinct_attribute: str | None = None
    typo_tolerance: dict[str, Any] | None = None
    faceting: dict[str, Any] | None = None
    pagination: dict[str, Any] | None = None
    search_cutoff_ms: int | None = None
