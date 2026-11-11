"""JSON pointers into a block's settings (RFC 6901), with one extension.

The catalogue names translatable and reference settings by pointer within
a block's ``settings``. A segment ``*`` stands for every array element or
object key at that level (``/items/*/label``), so one declaration covers a
list. A pointer without ``*`` names one place.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

WILDCARD = "*"

Path = tuple[str | int, ...]


def parse(pointer: str) -> tuple[str, ...]:
    """The segments of ``pointer`` (``/a/b`` -> ``("a", "b")``)."""
    if pointer == "":
        return ()
    if not pointer.startswith("/"):
        raise ValueError(f"not a JSON pointer: {pointer!r}")
    return tuple(
        part.replace("~1", "/").replace("~0", "~")
        for part in pointer[1:].split("/")
    )


def format_path(path: tuple[str | int, ...]) -> str:
    return "".join(
        "/" + str(part).replace("~", "~0").replace("/", "~1") for part in path
    )


def _children(value: Any) -> Iterator[tuple[str | int, Any]]:
    if isinstance(value, dict):
        yield from value.items()
    elif isinstance(value, list):
        yield from enumerate(value)


def walk(data: Any, pattern: tuple[str, ...]) -> Iterator[tuple[Path, Any]]:
    """Every ``(concrete path, value)`` in ``data`` that ``pattern`` names.

    A place that does not exist is not yielded: an optional setting left
    out has nothing to check.
    """
    if not pattern:
        yield (), data
        return
    head, rest = pattern[0], pattern[1:]
    if head == WILDCARD:
        for key, child in _children(data):
            for path, value in walk(child, rest):
                yield (key, *path), value
        return
    if isinstance(data, dict) and head in data:
        for path, value in walk(data[head], rest):
            yield (head, *path), value
    elif isinstance(data, list) and head.isdigit() and int(head) < len(data):
        index = int(head)
        for path, value in walk(data[index], rest):
            yield (index, *path), value


def matches(pattern: tuple[str, ...], concrete: tuple[str, ...]) -> bool:
    """Whether the concrete pointer ``concrete`` is one ``pattern`` names."""
    return len(pattern) == len(concrete) and all(
        want in (WILDCARD, have)
        for want, have in zip(pattern, concrete, strict=True)
    )


def get(data: Any, path: Path) -> Any:
    for part in path:
        data = data[part]
    return data


def set_at(data: Any, path: Path, value: Any) -> None:
    """Replace the value at ``path``; its parent must exist."""
    parent = get(data, path[:-1])
    parent[path[-1]] = value


def resolve_concrete(data: Any, segments: tuple[str, ...]) -> Path | None:
    """``segments`` as a path into ``data`` (list indexes become ints), or
    ``None`` when its parent does not exist. The last segment may be new."""
    path: list[str | int] = []
    cursor = data
    for position, segment in enumerate(segments):
        last = position == len(segments) - 1
        if isinstance(cursor, dict):
            if segment not in cursor and not last:
                return None
            path.append(segment)
            cursor = cursor.get(segment)
        elif isinstance(cursor, list) and segment.isdigit():
            index = int(segment)
            if index >= len(cursor):
                return None
            path.append(index)
            cursor = cursor[index]
        else:
            return None
    return tuple(path)
