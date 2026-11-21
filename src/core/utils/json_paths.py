"""Paths into a JSON object: a key, or keys into nested objects.

Used where a JSON column holds a person's data at some keys only (an
order's ``metadata``, a shipment's), so erasure removes and an export
lists exactly those (``Order.PERSONAL_METADATA``).
"""

from __future__ import annotations

from typing import Any

type JsonPath = tuple[str, ...]


def _parent(data: Any, path: JsonPath) -> Any:
    node = data
    for key in path[:-1]:
        node = node.get(key) if isinstance(node, dict) else None
    return node


def drop_paths(data: Any, paths: tuple[JsonPath, ...]) -> bool:
    """Remove each path from ``data`` in place; whether one was there."""
    removed = False
    for path in paths:
        parent = _parent(data, path)
        if isinstance(parent, dict) and path[-1] in parent:
            del parent[path[-1]]
            removed = True
    return removed


def pick_paths(data: Any, paths: tuple[JsonPath, ...]) -> dict[str, Any]:
    """The parts of ``data`` at ``paths``, nested as they are there; a path
    that is absent is left out."""
    picked: dict[str, Any] = {}
    for path in paths:
        parent = _parent(data, path)
        if not isinstance(parent, dict) or path[-1] not in parent:
            continue
        target = picked
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = parent[path[-1]]
    return picked
