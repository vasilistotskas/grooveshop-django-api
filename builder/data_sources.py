"""The page builder's typed data props: what a setting may hold when a block
declares it as data, and what that value points at.

A block declares a setting in the catalogue's ``data`` map as either

- ``{field: "ref", kind, onMissing}``: the setting holds the id of one record
  (a slug for a page), or
- ``{field: "query", source, onEmpty, maxLimit}``: the setting holds a
  descriptor of a list of records, a hand-picked list or a rule.

This module is the Django half of the contract: it validates a value against
its declaration and derives from it the exact references (records that must
exist, so a delete is warned about and a publish refuses a missing one) and
the query dependencies (facets of a list, so a change to a record reaches the
pages whose list it can enter or leave). The storefront runs the same cases
(``tests/fixtures/builder/data-cases.json``, copied and pinned, see
``.github/scripts/sync_upstream_sources.py``); the vocabulary below is held
equal to its by them.

A dependency is ``(query kind, facet)``: ``*`` for a list that no record can
be kept out of by a filter, ``category:<id>`` and ``parent:<id>`` for a list
bounded by one. One row per facet of the descriptor: a change is matched by
ANY of them, which can over-purge and never under-purge.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TypeGuard

from django.utils.translation import gettext as _

from builder import references
from builder.models import ReferenceKind

#: The codes a value can fail with (``PublishProblem.code``).
INVALID = "invalid_data_value"
UNKNOWN_FILTER = "unknown_filter"
TOO_MANY_IDS = "too_many_ids"

#: What a query of each source depends on, as a query kind.
QUERY_KIND = {
    "product": ReferenceKind.QUERY_PRODUCT,
    "blogPost": ReferenceKind.QUERY_BLOG_POST,
    "category": ReferenceKind.QUERY_CATEGORY,
    "blogCategory": ReferenceKind.QUERY_BLOG_CATEGORY,
    "promotion": ReferenceKind.QUERY_PROMOTION,
}

#: The orderings a rule over a source offers.
ORDERINGS = {
    "product": (
        "newest",
        "mostViewed",
        "mostLiked",
        "topRated",
        "discounted",
        "priceAsc",
        "priceDesc",
    ),
    "blogPost": ("newest", "mostViewed"),
}

ANY = "*"


def message(code: str) -> str:
    """The sentence for a data problem, in the active language."""
    return {
        INVALID: _("This value is not valid here."),
        UNKNOWN_FILTER: _("This list does not take that filter."),
        TOO_MANY_IDS: _("There are more ids than a list may name."),
        "too_many_queries": _("The document asks for too many lists."),
    }[code]


@dataclass(frozen=True, slots=True)
class DataLimits:
    """The caps the catalogue states for typed data props (``dataLimits``):
    the one place they live, in both repositories."""

    max_picked_ids: int
    max_filter_ids: int
    max_queries_per_document: int
    max_query_limit: int

    @classmethod
    def parse(cls, raw: Any) -> DataLimits:
        limits = cls(
            max_picked_ids=raw["maxPickedIds"],
            max_filter_ids=raw["maxFilterIds"],
            max_queries_per_document=raw["maxQueriesPerDocument"],
            max_query_limit=raw["maxQueryLimit"],
        )
        if not all(
            _is_int(value) and value >= 1
            for value in (
                limits.max_picked_ids,
                limits.max_filter_ids,
                limits.max_queries_per_document,
                limits.max_query_limit,
            )
        ):
            raise ValueError("dataLimits must be whole numbers of 1 or more")
        return limits


@dataclass(frozen=True, slots=True)
class Problem:
    path: str
    code: str


@dataclass(slots=True)
class Outcome:
    problems: list[Problem] = field(default_factory=list)
    #: ``(kind, target)`` of the records the value names.
    refs: set[tuple[str, str]] = field(default_factory=set)
    #: ``(query kind, facet)`` of the lists the value asks for.
    deps: set[tuple[str, str]] = field(default_factory=set)

    @property
    def valid(self) -> bool:
        return not self.problems

    def add(self, path: str, code: str) -> None:
        self.problems.append(Problem(path, code))


def _is_int(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _record_id(value: Any) -> bool:
    return references.accepts_value(ReferenceKind.PRODUCT, value)


def _ids(
    out: Outcome, path: str, value: Any, *, cap: int, kind: str
) -> list[int]:
    """A non-empty list of record ids, at most ``cap`` of them; each is a
    reference of ``kind``. Anything else is a problem at ``path``."""
    if not isinstance(value, list) or not value:
        out.add(path, INVALID)
        return []
    if len(value) > cap:
        out.add(path, TOO_MANY_IDS)
        return []
    if not all(_record_id(item) for item in value):
        out.add(path, INVALID)
        return []
    for item in value:
        out.refs.add((kind, str(item)))
    return list(value)


def _limit(out: Outcome, value: Mapping[str, Any], max_limit: int) -> None:
    limit = value.get("limit")
    if not _is_int(limit) or not 1 <= limit <= max_limit:
        out.add("/limit", INVALID)


def _only(out: Outcome, value: Mapping[str, Any], allowed: set[str]) -> None:
    for key in value:
        if key not in allowed:
            out.add(f"/{key}", UNKNOWN_FILTER)


def _picked(
    out: Outcome, value: Mapping[str, Any], limits: DataLimits, kind: str
) -> None:
    _only(out, value, {"mode", "ids"})
    _ids(out, "/ids", value.get("ids"), cap=limits.max_picked_ids, kind=kind)


def _ordering(out: Outcome, value: Mapping[str, Any], source: str) -> None:
    if value.get("ordering") not in ORDERINGS[source]:
        out.add("/ordering", INVALID)


def _product(
    out: Outcome,
    value: Mapping[str, Any],
    max_limit: int,
    query: str,
    limits: DataLimits,
) -> None:
    mode = value.get("mode")
    if mode == "picked":
        _picked(out, value, limits, ReferenceKind.PRODUCT)
    elif mode == "filter":
        _only(out, value, {"mode", "categories", "ordering", "limit"})
        _ordering(out, value, "product")
        _limit(out, value, max_limit)
        if "categories" in value:
            for category in _ids(
                out,
                "/categories",
                value["categories"],
                cap=limits.max_filter_ids,
                kind=ReferenceKind.CATEGORY,
            ):
                out.deps.add((query, f"category:{category}"))
        if not out.deps and out.valid:
            out.deps.add((query, ANY))
    else:
        out.add("", INVALID)


def _blog_post(
    out: Outcome,
    value: Mapping[str, Any],
    max_limit: int,
    query: str,
    limits: DataLimits,
) -> None:
    mode = value.get("mode")
    if mode == "picked":
        _picked(out, value, limits, ReferenceKind.BLOG_POST)
    elif mode == "filter":
        _only(out, value, {"mode", "category", "featured", "ordering", "limit"})
        _ordering(out, value, "blogPost")
        _limit(out, value, max_limit)
        if "featured" in value and not isinstance(value["featured"], bool):
            out.add("/featured", INVALID)
        if "category" in value:
            category = value["category"]
            if _record_id(category):
                out.refs.add((ReferenceKind.BLOG_CATEGORY, str(category)))
                out.deps.add((query, f"category:{category}"))
            else:
                out.add("/category", INVALID)
        if not out.deps and out.valid:
            out.deps.add((query, ANY))
    else:
        out.add("", INVALID)


def _category(
    out: Outcome,
    value: Mapping[str, Any],
    max_limit: int,
    query: str,
    limits: DataLimits,
) -> None:
    mode = value.get("mode")
    if mode not in ("roots", "children"):
        out.add("", INVALID)
        return
    _only(
        out,
        value,
        {"mode", "limit"} | ({"parent"} if mode == "children" else set()),
    )
    _limit(out, value, max_limit)
    if mode == "roots":
        if out.valid:
            out.deps.add((query, ANY))
        return
    parent = value.get("parent")
    if not _record_id(parent):
        out.add("/parent", INVALID)
    elif out.valid:
        out.refs.add((ReferenceKind.CATEGORY, str(parent)))
        out.deps.add((query, f"parent:{parent}"))


def _just_a_limit(
    out: Outcome,
    value: Mapping[str, Any],
    max_limit: int,
    query: str,
    limits: DataLimits,
) -> None:
    _only(out, value, {"limit"})
    _limit(out, value, max_limit)
    if out.valid:
        out.deps.add((query, ANY))


_QUERIES = {
    "product": _product,
    "blogPost": _blog_post,
    "category": _category,
    "blogCategory": _just_a_limit,
    "promotion": _just_a_limit,
}


def check(
    declaration: Mapping[str, Any], value: Any, limits: DataLimits
) -> Outcome:
    """Judge *value* against *declaration* (a ``data`` entry of the
    catalogue) and say what it names and depends on. A value that is not
    valid yields no reference and no dependency."""
    out = Outcome()
    if declaration.get("field") == "ref":
        kind = str(declaration["kind"])
        if references.accepts_value(kind, value):
            out.refs.add((kind, str(value)))
        else:
            out.add("", INVALID)
        return out
    source = str(declaration["source"])
    if not isinstance(value, dict):
        out.add("", INVALID)
        return out
    _QUERIES[source](
        out,
        value,
        min(int(declaration["maxLimit"]), limits.max_query_limit),
        QUERY_KIND[source],
        limits,
    )
    if not out.valid:
        out.refs.clear()
        out.deps.clear()
    return out
