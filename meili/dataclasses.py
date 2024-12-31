from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class MeiliIndexSettings:
    displayed_fields: Optional[list[str]] = None
    searchable_fields: Optional[list[str]] = None
    filterable_fields: Optional[list[str]] = None
    sortable_fields: Optional[list[str]] = None
    ranking_rules: Optional[list[str]] = None
    stop_words: Optional[list[str]] = None
    synonyms: Optional[dict[str, list[str]]] = None
    distinct_attribute: Optional[str] = None
    typo_tolerance: Optional[dict[str, Any]] = None
    faceting: Optional[dict[str, Any]] = None
    pagination: Optional[dict[str, Any]] = None
