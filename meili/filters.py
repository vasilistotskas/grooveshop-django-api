r"""Filter expressions the engine is sent, built the one safe way.

Values are always double-quoted with ``\`` and ``"`` escaped. Meilisearch
unescapes a backslash inside a quoted filter value (since v1.4 a literal
backslash must be written ``\``), so a document id can never close the
quote or add a clause of its own.

See https://www.meilisearch.com/docs/learn/filtering_and_sorting/filter_expression_reference
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_ATTRIBUTE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def quote_value(value: object) -> str:
    """``value`` as a double-quoted filter string."""
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def primary_key_filter(primary_key: str, ids: Iterable[object]) -> str:
    """``<primary_key> IN ["a", "b"]`` for a delete by filter.

    ``primary_key`` must be filterable (``MeiliIndexSettings`` always adds
    it, equality only). An empty ``ids`` is refused: ``IN []`` is not a
    delete anyone means to send.
    """
    if not _ATTRIBUTE.fullmatch(primary_key):
        raise ValueError(f"Not a filterable attribute name: {primary_key!r}")
    values = [quote_value(value) for value in ids]
    if not values:
        raise ValueError("No document ids to build a filter from")
    return f"{primary_key} IN [{', '.join(values)}]"
