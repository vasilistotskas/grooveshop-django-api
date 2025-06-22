from dataclasses import dataclass
from typing import Any


@dataclass
class MeiliIndexSettings:
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
