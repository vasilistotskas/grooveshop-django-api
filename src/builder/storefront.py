"""The boundary to the storefront: the catalogue of blocks a store renders.

The storefront declares every section and block once (a Zod schema, its
version, which settings are translatable or references, its limits) and
serves the result per store:

``GET /api/builder/catalogue?schema=<schema>`` answers
``{hash, blocks: [...]}`` with ``ETag: "<hash>"``. The body is immutable per
build, so a process keeps its copy and revalidates with ``If-None-Match``
(a 304 carries no body). Django validates every save against that copy and
adds only what it alone can check (``builder.validation``).

Nothing is saved on a guess: when the storefront does not answer (a fetch or
a revalidation), :class:`StorefrontUnavailable` is raised and the caller
refuses, a copy held or not. The rule of ``texts.storefront``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import requests
from django.conf import settings
from jsonschema import Draft202012Validator
from jsonschema.protocols import Validator

from builder import contrast as contrast_module
from builder import data_sources, pointers
from builder.models import ReferenceKind
from core.storefront import StorefrontUnavailable, base_url

logger = logging.getLogger(__name__)

#: Where a section or a block may be placed.
KIND_SECTION = "section"
KIND_BLOCK = "block"

REFERENCE_KINDS = frozenset(ReferenceKind.values)


@dataclass(frozen=True, slots=True)
class BlockDefinition:
    """One block or section type of the storefront's build."""

    type: str
    version: int
    accepts: frozenset[int]
    schema: dict[str, Any]
    translatable: tuple[str, ...]
    rich_text: tuple[str, ...]
    refs: dict[str, str]
    #: ``data``: pointer -> declaration of a setting that is a typed data
    #: prop (``builder.data_sources``).
    data: dict[str, dict[str, Any]]
    #: Pointer patterns (``linkRefs``) of the settings that are links: ``{href}``,
    #: ``{route}`` or ``{ref: {kind, id}}``. Only the last one points at
    #: something of ours, and its kind is in the data, not here.
    link_refs: tuple[str, ...]
    max_blocks: int | None
    max_per_template: int | None
    children_allowed: frozenset[str]
    children_max_depth: int | None
    kinds: frozenset[str]
    groups: frozenset[str] | None
    templates: frozenset[str] | None
    feature: str | None
    deprecated: bool
    validator: Validator = field(compare=False, repr=False)

    @property
    def versioned_type(self) -> str:
        return f"{self.type}@{self.version}"


@dataclass(frozen=True, slots=True)
class RootDefinition:
    """The settings a kind of document carries at its root (a page's SEO
    fields), declared by the catalogue's ``documentKinds``. It answers the
    same questions as a :class:`BlockDefinition` for its ``settings``."""

    schema: dict[str, Any]
    translatable: tuple[str, ...]
    rich_text: tuple[str, ...]
    refs: dict[str, str]
    data: dict[str, dict[str, Any]]
    link_refs: tuple[str, ...]
    #: The pairs of colours a theme must keep a contrast between
    #: (``builder.contrast``); empty for every other kind of document.
    contrast: tuple[contrast_module.Rule, ...]
    #: ``defaults``: the settings a NEW store of this view's design starts
    #: with (the theme's), or ``None`` when the catalogue states none.
    defaults: dict[str, Any] | None
    validator: Validator = field(compare=False, repr=False)


#: What a settings object is validated against: a block's or a document root's.
Spec = BlockDefinition | RootDefinition


@dataclass(frozen=True, slots=True)
class Catalogue:
    hash: str
    blocks: tuple[BlockDefinition, ...]
    roots: dict[str, RootDefinition] = field(default_factory=dict)
    #: The page types that are fixed templates (home, about, ...); any
    #: other page is a custom ``page:<key>``. The storefront's routing.
    templates: frozenset[str] = frozenset()
    #: The caps for typed data props (``dataLimits``); the catalogue states
    #: them whenever a block declares ``data``.
    data_limits: data_sources.DataLimits | None = None
    _index: dict[str, BlockDefinition] = field(
        default_factory=dict, compare=False, repr=False
    )

    def get(self, type_name: str) -> BlockDefinition | None:
        return self._index.get(type_name)


def _optional_set(raw: Any) -> frozenset[str] | None:
    return None if raw is None else frozenset(str(item) for item in raw)


def _rich_text_pointers(
    schema: Any, prefix: tuple[str, ...] = ()
) -> Iterator[str]:
    """Pointers (``*`` for a list's elements) of the settings whose schema
    says ``contentMediaType: text/html``: those are rich text, held to the
    sanitiser policy. Only inline schemas are walked, as the catalogue's
    are (``builder/pointers.py``)."""
    if not isinstance(schema, dict):
        return
    if schema.get("contentMediaType") == "text/html":
        yield "".join(
            "/" + part.replace("~", "~0").replace("/", "~1") for part in prefix
        )
    for name, sub in (schema.get("properties") or {}).items():
        yield from _rich_text_pointers(sub, (*prefix, name))
    yield from _rich_text_pointers(schema.get("items"), (*prefix, "*"))
    for combinator in ("anyOf", "oneOf", "allOf"):
        for sub in schema.get(combinator) or ():
            yield from _rich_text_pointers(sub, prefix)


def _parse_block(raw: dict[str, Any]) -> BlockDefinition:
    schema = raw["schema"]
    limits = raw.get("limits") or {}
    children = raw.get("children") or {}
    refs = _parse_refs(raw.get("refs"))
    translatable = tuple(str(p) for p in raw.get("translatable") or ())
    return BlockDefinition(
        type=str(raw["type"]),
        version=int(raw["version"]),
        accepts=frozenset(int(v) for v in raw.get("accepts") or ()),
        schema=schema,
        translatable=translatable,
        rich_text=tuple(dict.fromkeys(_rich_text_pointers(schema))),
        refs=refs,
        data=_parse_data(raw.get("data")),
        link_refs=_parse_link_refs(raw["linkRefs"], translatable),
        max_blocks=limits.get("maxBlocks"),
        max_per_template=limits.get("maxPerTemplate"),
        children_allowed=frozenset(
            str(t) for t in children.get("allowed") or ()
        ),
        children_max_depth=children.get("maxDepth"),
        kinds=frozenset(raw["kinds"]),
        groups=_optional_set(raw.get("groups")),
        templates=_optional_set(raw.get("templates")),
        feature=raw.get("feature"),
        deprecated=bool(raw.get("deprecated", False)),
        validator=Draft202012Validator(
            schema, format_checker=Draft202012Validator.FORMAT_CHECKER
        ),
    )


def _overlaps(first: str, second: str) -> bool:
    """Whether two pointer patterns can name the same place or one lies
    under the other (``*`` matches any one segment)."""
    return all(
        a == b or pointers.WILDCARD in (a, b)
        for a, b in zip(pointers.parse(first), pointers.parse(second))
    )


def _parse_link_refs(
    raw: Any, translatable: tuple[str, ...]
) -> tuple[str, ...]:
    """The link pointers of a block or a root, a required key (``[]`` for
    none): a catalogue that left it out would index no link at all. A link
    resolves to the path of the viewer's language, so it is never
    translatable, and neither is anything in it or around it: a link pointer
    that equals, lies under or holds a translatable pointer is a catalogue
    bug, refused when the catalogue is read."""
    link_refs = tuple(str(p) for p in raw)
    if both := sorted(
        (link, text)
        for link in link_refs
        for text in translatable
        if _overlaps(link, text)
    ):
        raise ValueError(f"link settings cannot be translatable: {both}")
    return link_refs


def _parse_data(raw: Any) -> dict[str, dict[str, Any]]:
    """The ``data`` map of a block: ``{pointer: declaration}``. A declaration
    the catalogue got wrong is a catalogue bug, refused when it is read."""
    data = {str(k): dict(v) for k, v in (raw or {}).items()}
    for pointer, declaration in data.items():
        field_ = declaration.get("field")
        if field_ == "ref":
            known = declaration.get("kind") in REFERENCE_KINDS
        elif field_ == "query":
            known = declaration.get("source") in data_sources.QUERY_KIND and (
                isinstance(declaration.get("maxLimit"), int)
            )
        else:
            known = False
        if not known:
            raise ValueError(f"unknown data declaration at {pointer}")
    return data


def _parse_refs(raw: Any) -> dict[str, str]:
    refs = {str(k): str(v) for k, v in (raw or {}).items()}
    if not set(refs.values()) <= REFERENCE_KINDS:
        raise ValueError("unknown reference kind")
    return refs


def _parse_root(raw: dict[str, Any]) -> RootDefinition:
    schema = raw["schema"]
    translatable = tuple(str(p) for p in raw.get("translatable") or ())
    return RootDefinition(
        schema=schema,
        translatable=translatable,
        rich_text=tuple(dict.fromkeys(_rich_text_pointers(schema))),
        refs=_parse_refs(raw.get("refs")),
        data=_parse_data(raw.get("data")),
        link_refs=_parse_link_refs(raw["linkRefs"], translatable),
        contrast=contrast_module.parse_rules(raw.get("contrast")),
        defaults=(
            dict(raw["defaults"]) if raw.get("defaults") is not None else None
        ),
        validator=Draft202012Validator(
            schema, format_checker=Draft202012Validator.FORMAT_CHECKER
        ),
    )


def parse_catalogue(payload: Any) -> Catalogue:
    try:
        blocks = tuple(_parse_block(raw) for raw in payload["blocks"])
        roots = {
            str(kind): _parse_root(raw)
            for kind, raw in (payload.get("documentKinds") or {}).items()
        }
        declares_data = any(b.data for b in blocks) or any(
            r.data for r in roots.values()
        )
        raw_limits = payload.get("dataLimits")
        if declares_data and raw_limits is None:
            raise ValueError("data is declared without dataLimits")
        return Catalogue(
            hash=str(payload["hash"]),
            blocks=blocks,
            roots=roots,
            templates=frozenset(str(t) for t in payload.get("templates") or ()),
            data_limits=(
                data_sources.DataLimits.parse(raw_limits)
                if raw_limits is not None
                else None
            ),
            _index={block.type: block for block in blocks},
        )
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise StorefrontUnavailable("unexpected catalogue shape") from exc


@dataclass(slots=True)
class _Held:
    etag: str
    checked_at: float
    catalogue: Catalogue


_held: dict[str, _Held] = {}
_lock = threading.Lock()


def clear_catalogue_cache() -> None:
    with _lock:
        _held.clear()


def fetch_catalogue(schema: str) -> Catalogue:
    """The catalogue of what store *schema* renders.

    Held per process and revalidated by ETag once
    ``BUILDER_CATALOGUE_REVALIDATE_SECONDS`` have passed, so the hot paths
    cost a dict lookup and a deploy of the storefront is picked up within
    that window. Exactly the rule of ``texts``: when the storefront does not
    answer a revalidation, nothing is checked and the caller refuses, held
    copy or not.
    """
    now = time.monotonic()
    held = _held.get(schema)
    if (
        held is not None
        and now - held.checked_at
        < settings.BUILDER_CATALOGUE_REVALIDATE_SECONDS
    ):
        return held.catalogue

    headers = {"Accept": "application/json"}
    if held is not None:
        headers["If-None-Match"] = held.etag
    try:
        response = requests.get(
            f"{base_url()}/api/builder/catalogue",
            params={"schema": schema},
            headers=headers,
            timeout=settings.BUILDER_STOREFRONT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Builder catalogue unavailable: %s", exc)
        raise StorefrontUnavailable(str(exc)) from exc

    if response.status_code == 304 and held is not None:
        held.checked_at = now
        return held.catalogue
    if response.status_code != 200:
        logger.warning("Builder catalogue answered %s", response.status_code)
        raise StorefrontUnavailable(
            f"catalogue answered {response.status_code}"
        )
    try:
        catalogue = parse_catalogue(response.json())
    except ValueError as exc:
        raise StorefrontUnavailable("catalogue is not JSON") from exc

    with _lock:
        _held[schema] = _Held(
            etag=response.headers.get("ETag") or f'"{catalogue.hash}"',
            checked_at=now,
            catalogue=catalogue,
        )
    return catalogue
