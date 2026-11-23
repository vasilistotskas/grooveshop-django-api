"""Paths into a JSON object: a key, or keys into nested objects.

Used where a JSON column holds a person's data at some keys only (an
order's ``metadata``, a shipment's), so erasure removes and an export
lists exactly those (``Order.PERSONAL_METADATA``). ``"*"`` in a path
stands for every item of a list there (``("cancellations", "*",
"reason")``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

type JsonPath = tuple[str, ...]

ANY_ITEM = "*"


def _parents(node: Any, keys: JsonPath) -> Iterator[dict[str, Any]]:
    """The objects the path's last key is looked up in."""
    if not keys:
        if isinstance(node, dict):
            yield node
        return
    key, rest = keys[0], keys[1:]
    if key == ANY_ITEM:
        for item in node if isinstance(node, list) else ():
            yield from _parents(item, rest)
    elif isinstance(node, dict):
        yield from _parents(node.get(key), rest)


def drop_paths(data: Any, paths: tuple[JsonPath, ...]) -> bool:
    """Remove each path from ``data`` in place; whether one was there."""
    removed = False
    for path in paths:
        for parent in _parents(data, path[:-1]):
            if path[-1] in parent:
                del parent[path[-1]]
                removed = True
    return removed


def pick_paths(data: Any, paths: tuple[JsonPath, ...]) -> dict[str, Any]:
    """The parts of ``data`` at ``paths``, nested as they are there; a path
    that is absent is left out. Paths with :data:`ANY_ITEM` are not
    picked: an export lists those from their own records."""
    picked: dict[str, Any] = {}
    for path in paths:
        if ANY_ITEM in path:
            raise ValueError(f"{path} names list items")
        node = data
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
        if not isinstance(node, dict) or path[-1] not in node:
            continue
        target = picked
        for key in path[:-1]:
            target = target.setdefault(key, {})
        target[path[-1]] = node[path[-1]]
    return picked
