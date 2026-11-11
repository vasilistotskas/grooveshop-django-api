"""Find the images that builder documents still name by URL, and bring them
into the media library.

Before the media picker a block held an image as a string (``/img/x.jpg``,
``/uploads/y.jpg``). This module scans EVERY string of a document, by value
and never by field name, because the catalogue (the storefront's) does not
mark those fields. It reads the drafts and the published versions, finds the
paths of this store, and imports each one through the library's own import
(``builder.media_services``), so the checks, the shrinking of an oversize
photo and the de-duplication by content are the ones of an upload. The result
is a map of each URL to the ``MediaAsset`` it became.

A string is an image path of this store when it starts with ``/img/`` (the
storefront's own files), ``/uploads/`` or ``/media/<this store>/uploads/``, the
last also as Django itself writes it, with no leading slash, and ends in an
image extension. An absolute ``http(s)`` address on one of the store's own
hosts (:func:`own_hosts`) is the path it names, and the file is read from
where the store keeps it like any path. Any other absolute address with an
image extension is EXTERNAL: it is listed and never fetched.

:func:`migrate_document` is what puts the asset in a document in place of the
URL, for the settings the storefront's blocks declare as old ones
(``legacy_image_fields.json``): ``manage.py migrate_builder_media``.
:func:`resolve` finds the asset of each of those URLs, and names each one that
has none for an operator to decide (:class:`Decisions`).
"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings
from django.db import connection

from builder import media_services, pointers
from builder.block_upgrades import nodes_of
from builder.document_rewrite import DocumentRefused
from builder.errors import BuilderError
from builder.models import BuilderDocument, BuilderVersion, MediaAsset
from builder.services import published_versions
from builder.validation import VERSIONED_TYPE
from tenant.membership import bound_tenant

#: The storefront's declaration of each block's old image settings, vendored and
#: pinned like the catalogue (``sync_upstream_sources.py``).
LEGACY_FIELDS_FILE = Path(__file__).with_name("legacy_image_fields.json")

IMAGE_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".avif",
    ".tif",
    ".tiff",
    ".svg",
)
#: A scheme (``https:``) or a protocol-relative start (``//host``).
_ABSOLUTE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*:|//)")

STATIC = "static"
STORE = "store"
EXTERNAL = "external"


@dataclass(frozen=True, slots=True)
class Found:
    """One string of a document that names an image."""

    document: str
    #: ``draft`` or ``version <number>``.
    source: str
    #: JSON pointer into the document (``i18n`` ones start ``/i18n``).
    path: str
    url: str
    kind: str
    #: The path the library reads it from; none for an external one.
    import_url: str = ""


@dataclass(slots=True)
class Outcome:
    """What importing one internal URL did (or, in a dry run, would do)."""

    status: str  # new | existing | failed
    asset_id: int | None = None
    detail: str = ""


@dataclass(slots=True)
class Plan:
    found: list[Found] = field(default_factory=list)

    @property
    def internal(self) -> dict[str, list[Found]]:
        """``{import url: where it is}`` for the images of this store."""
        grouped: dict[str, list[Found]] = defaultdict(list)
        for item in self.found:
            if item.kind != EXTERNAL:
                grouped[item.import_url].append(item)
        return dict(sorted(grouped.items()))

    @property
    def external(self) -> list[Found]:
        return [item for item in self.found if item.kind == EXTERNAL]


def _has_image_extension(path: str) -> bool:
    return path.lower().split("?", 1)[0].endswith(IMAGE_EXTENSIONS)


def _rooted(text: str) -> str:
    """*text*, the leading slash added to a path as Django writes it
    (``media/<store>/uploads/x.png``)."""
    return f"/{text}" if text.startswith("media/") else text


@dataclass(frozen=True, slots=True)
class OwnHosts:
    """The hosts on which an absolute address names one of this store's own
    files."""

    #: The store's own domains: every path there is the store's.
    store: frozenset[str] = frozenset()
    #: The platform's media and static hosts, which every store shares and
    #: where a store's files are told apart by path: only ``/media/...``.
    platform: frozenset[str] = frozenset()


def own_hosts() -> OwnHosts:
    """The current store's :class:`OwnHosts`: its ``TenantDomain`` rows, read
    now, and the hosts of ``MEDIA_STREAM_BASE_URL`` and ``STATIC_BASE_URL``."""
    domains = getattr(bound_tenant(), "domains", None)
    store = frozenset(
        domain.lower()
        for domain in (
            domains.values_list("domain", flat=True)
            if domains is not None
            else ()
        )
    )
    shared = (settings.MEDIA_STREAM_BASE_URL, settings.STATIC_BASE_URL)
    platform = frozenset(
        host.lower() for base in shared if (host := urlsplit(base).hostname)
    )
    return OwnHosts(store, platform - store)


def _is_web_address(text: str) -> bool:
    return text.lower().startswith(("http:", "https:", "//"))


def _local(text: str, hosts: OwnHosts) -> str:
    """*text* as the path it names, when it is an address on one of the
    store's own hosts; else as it is. Only the host is taken off: a query
    stays, for the import to refuse as it refuses one on a bare path."""
    if not _is_web_address(text):
        return text
    try:
        parts = urlsplit(text)
        host = (parts.hostname or "").lower()
    except ValueError:
        return text
    path = parts._replace(scheme="", netloc="").geturl()
    if host in hosts.store:
        return path
    if host in hosts.platform and path.startswith("/media/"):
        return path
    return text


def _kind_of_path(text: str, schema: str) -> str | None:
    if text.startswith("/img/"):
        return STATIC
    if text.startswith(("/uploads/", f"/media/{schema}/uploads/")):
        return STORE
    if text.startswith("/media/"):
        # Another store's file: not ours to copy.
        return EXTERNAL
    return None


def classify(value: str, schema: str, hosts: OwnHosts) -> str | None:
    """``static``, ``store``, ``external`` or ``None`` (not an image path)."""
    text = _rooted(value.strip())
    if not text or len(text) > 1000 or not _has_image_extension(text):
        return None
    if not _ABSOLUTE.match(text):
        return _kind_of_path(text, schema)
    if not _is_web_address(text):
        return None
    local = _local(text, hosts)
    kind = None if local == text else _kind_of_path(local, schema)
    return kind or EXTERNAL


def import_url_of(value: str, hosts: OwnHosts) -> str:
    """The path ``media_services`` reads: ``/uploads/x`` is
    ``/media/<store>/uploads/x``, and an address on one of the store's own
    hosts is its path."""
    text = _local(_rooted(value.strip()), hosts)
    if text.startswith("/uploads/"):
        return f"/media/{connection.schema_name}{text}"
    return text


def _found(
    document: str, source: str, path: str, url: str, kind: str, hosts: OwnHosts
) -> Found:
    return Found(
        document,
        source,
        path,
        url,
        kind,
        "" if kind == EXTERNAL else import_url_of(url, hosts),
    )


def _strings(data: Any) -> Iterator[tuple[str, str]]:
    """Every ``(json pointer, string)`` leaf of *data*."""

    def walk(value: Any, path: tuple[str | int, ...]) -> Iterator[Any]:
        if isinstance(value, str):
            yield pointers.format_path(path), value
        elif isinstance(value, dict):
            for key, child in value.items():
                yield from walk(child, (*path, key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                yield from walk(child, (*path, index))

    yield from walk(data, ())


#: Documents read at a time: a version carries its whole content, and a store
#: holds hundreds of them.
READ_CHUNK = 50


def _drafts() -> Iterator[BuilderDocument]:
    """Every document of this store, a deleted page too: its versions are
    still stored, and are moved with the rest."""
    return (
        BuilderDocument.objects.all_with_deleted()
        .order_by("key")
        .iterator(chunk_size=READ_CHUNK)
    )


def scan(*, all_versions: bool = False) -> Plan:
    """Every image string in the drafts and the published versions of this
    store (every version with ``all_versions``)."""
    schema = connection.schema_name
    hosts = own_hosts()
    plan = Plan()

    def collect(document: str, source: str, data: Any, prefix: str) -> None:
        for path, value in _strings(data):
            kind = classify(value, schema, hosts)
            if kind is not None:
                plan.found.append(
                    _found(
                        document,
                        source,
                        prefix + path,
                        value.strip(),
                        kind,
                        hosts,
                    )
                )

    for doc in _drafts():
        collect(doc.key, "draft", doc.draft, "")
        collect(doc.key, "draft", doc.draft_i18n, "/i18n")
    versions = BuilderVersion.objects.select_related("document").order_by(
        "document__key", "number"
    )
    if not all_versions:
        versions = versions.filter(pk__in=published_versions().values())
    for version in versions.iterator(chunk_size=READ_CHUNK):
        source = f"version {version.number}"
        collect(version.document.key, source, version.data, "")
        collect(version.document.key, source, version.i18n, "/i18n")
    return plan


def run(plan: Plan, *, dry_run: bool) -> dict[str, Outcome]:
    """Import (or, in a dry run, only check) each distinct image of this
    store. ``{import url: Outcome}``; one that cannot be read or is refused
    is ``failed`` with the reason, and the rest carry on."""
    outcomes: dict[str, Outcome] = {}
    for url in plan.internal:
        try:
            if dry_run:
                prepared = media_services.prepare(
                    media_services.read_import(url)
                )
                known = MediaAsset.objects.filter(
                    content_hash=prepared.digest
                ).first()
                outcomes[url] = (
                    Outcome("existing", known.pk) if known else Outcome("new")
                )
            else:
                stored = media_services.store_import(url)
                outcomes[url] = Outcome(
                    "new" if stored.created else "existing", stored.asset.pk
                )
        except BuilderError as exc:
            detail = exc.detail
            code = detail.get("code", "") if isinstance(detail, dict) else ""
            outcomes[url] = Outcome("failed", detail=str(code))
    return outcomes


def mapping(plan: Plan, outcomes: dict[str, Outcome]) -> dict[str, int]:
    """``{url as written in a document: asset id}`` for every imported image."""
    result: dict[str, int] = {}
    for url, items in plan.internal.items():
        outcome = outcomes.get(url)
        if outcome is not None and outcome.asset_id is not None:
            for item in items:
                result[item.url] = outcome.asset_id
    return dict(sorted(result.items()))


@dataclass(frozen=True, slots=True)
class LegacyField:
    """One old image setting of a block, by the storefront's ``legacyImages``."""

    source: str
    target: str
    alt: str | None
    is_list: bool


def _setting_of(pointer: str) -> str:
    return pointer.removeprefix("/").removesuffix("/*")


@cache
def legacy_fields() -> dict[str, tuple[LegacyField, ...]]:
    """``{block type: its old image settings}``."""
    grouped: dict[str, list[LegacyField]] = defaultdict(list)
    for row in json.loads(LEGACY_FIELDS_FILE.read_text(encoding="utf-8")):
        alt = row.get("alt")
        grouped[row["block"]].append(
            LegacyField(
                source=_setting_of(row["from"]),
                target=_setting_of(row["to"]),
                alt=_setting_of(alt) if alt else None,
                is_list=row["list"],
            )
        )
    return {block: tuple(fields) for block, fields in grouped.items()}


def _fields_of(node: Any) -> tuple[LegacyField, ...]:
    versioned = node.get("type") if isinstance(node, dict) else None
    match = (
        VERSIONED_TYPE.match(versioned) if isinstance(versioned, str) else None
    )
    return legacy_fields().get(match.group(1), ()) if match else ()


@dataclass(frozen=True, slots=True)
class LegacyUse:
    """One image a document still names by URL, with the alt it gives it."""

    document: str
    #: ``draft`` or ``version <number>``.
    source: str
    path: str
    #: The node's ``type@version``.
    block: str
    url: str
    #: ``{language: text}``, what this use says in each language it says it.
    alt: Mapping[str, str]

    @property
    def where(self) -> str:
        return f"{self.document} {self.source}"


def _alt_of(
    node_id: str, node_settings: dict, name: str | None, i18n: dict
) -> dict[str, str]:
    """A use's alt per language: the setting is the default language's, and the
    overlay holds the rest."""
    if name is None:
        return {}
    texts = {settings.PARLER_DEFAULT_LANGUAGE_CODE: node_settings.get(name)}
    for locale, per_node in (i18n or {}).items():
        if locale != settings.PARLER_DEFAULT_LANGUAGE_CODE:
            texts[locale] = ((per_node or {}).get(node_id) or {}).get(
                f"/{name}"
            )
    return {
        locale: text.strip()
        for locale, text in texts.items()
        if isinstance(text, str) and text.strip()
    }


def legacy_uses(
    document: str, source: str, data: Any, i18n: Any
) -> Iterator[LegacyUse]:
    """Every old image setting that still names an image, in *data*. A node
    whose new setting is already chosen has none: its old one is left over."""
    for parent, node_id in nodes_of(data or {}, "sections"):
        node = parent[node_id]
        for legacy in _fields_of(node):
            node_settings = node.get("settings") or {}
            if node_settings.get(legacy.target):
                continue
            value = node_settings.get(legacy.source)
            values = value if isinstance(value, list) else [value]
            alt = _alt_of(node_id, node_settings, legacy.alt, i18n)
            for position, url in enumerate(values):
                if isinstance(url, str) and url.strip():
                    tail = f"/{position}" if legacy.is_list else ""
                    yield LegacyUse(
                        document,
                        source,
                        f"/sections/{node_id}/settings/{legacy.source}{tail}",
                        str(node["type"]),
                        url.strip(),
                        alt,
                    )


def stored_uses() -> tuple[list[LegacyUse], list[LegacyUse]]:
    """Every old image setting of this store: ``(what the drafts and the current
    release hold, what only older versions hold)``. Only the first decide an
    image's alt: an older version's text is what the store has since replaced."""
    current: list[LegacyUse] = []
    older: list[LegacyUse] = []
    for document in _drafts():
        current.extend(
            legacy_uses(
                document.key, "draft", document.draft, document.draft_i18n
            )
        )
    live = set(published_versions().values())
    versions = BuilderVersion.objects.select_related("document").order_by(
        "document__key", "number"
    )
    for version in versions.iterator(chunk_size=READ_CHUNK):
        (current if version.pk in live else older).extend(
            legacy_uses(
                version.document.key,
                f"version {version.number}",
                version.data,
                version.i18n,
            )
        )
    return current, older


#: What a decision writes for a value that is to become no image.
NO_IMAGE = "empty"
#: What a decision writes for an empty list entry whose position is to go from
#: every list of its block.
REMOVE_POSITION = "remove"


class DecisionsInvalid(ValueError):
    """The decisions file does not say what it must."""


@dataclass(frozen=True, slots=True)
class Decisions:
    """What an operator decided for one store, one by one. Nothing else
    settles a value the library cannot take, an alt its uses disagree on, or
    an empty entry of a list beside an image of the block's other list."""

    #: ``{url as a document writes it: the library image, None for none}``.
    images: Mapping[str, int | None] = field(default_factory=dict)
    #: ``{(asset id, language): the image's alt, empty for none}``.
    alts: Mapping[tuple[int, str], str] = field(default_factory=dict)
    #: ``{"<document> <draft | version N>: <pointer>" of an empty list entry:
    #: the library image to put there, None to remove its position}``.
    slots: Mapping[str, int | None] = field(default_factory=dict)

    def as_stated(self) -> dict[str, Any]:
        """These decisions as a decisions file states them, for the record of
        a run."""
        alt: dict[str, dict[str, str]] = defaultdict(dict)
        for (asset, locale), text in self.alts.items():
            alt[str(asset)][locale] = text
        stated = {
            "images": {
                url: NO_IMAGE if asset is None else asset
                for url, asset in self.images.items()
            },
            "alt": dict(alt),
            "slots": {
                slot: REMOVE_POSITION if asset is None else asset
                for slot, asset in self.slots.items()
            },
        }
        return {part: decided for part, decided in stated.items() if decided}


def _decisions_of(schema: str, stated: Any) -> Decisions:
    def bad(what: str) -> DecisionsInvalid:
        return DecisionsInvalid(f"{schema}: {what}")

    keys = ("images", "alt", "slots")
    if not isinstance(stated, dict) or set(stated) - set(keys):
        raise bad('a store holds "images", "alt" and "slots", nothing else')
    stated_images, stated_alts, stated_slots = (
        stated.get(key, {}) for key in keys
    )
    if not all(
        isinstance(part, dict)
        for part in (stated_images, stated_alts, stated_slots)
    ):
        raise bad('"images", "alt" and "slots" are objects')

    def image_or(word: str, part: str, stated_part: dict) -> dict:
        """``{key: image id, None for *word*}`` of one part of the file."""
        decided: dict[str, int | None] = {}
        for key, asset in stated_part.items():
            if asset == word:
                decided[key.strip()] = None
            elif type(asset) is int and asset > 0:
                decided[key.strip()] = asset
            else:
                raise bad(
                    f'{part}[{key!r}] is an image id of the library or "{word}"'
                )
        return decided

    images = image_or(NO_IMAGE, "images", stated_images)
    slots = image_or(REMOVE_POSITION, "slots", stated_slots)
    alts: dict[tuple[int, str], str] = {}
    for asset, texts in stated_alts.items():
        if not asset.isdecimal() or not isinstance(texts, dict):
            raise bad(f"alt[{asset!r}] is an image id to {{language: text}}")
        for locale, alt in texts.items():
            if not isinstance(alt, str):
                raise bad(f"alt[{asset!r}][{locale!r}] is a text")
            alts[int(asset), locale] = alt.strip()
    return Decisions(images, alts, slots)


def read_decisions(path: Path) -> dict[str, Decisions]:
    """``{store schema: its decisions}`` of the file an operator wrote. A
    store holds ``images``, each value as a document writes it to the id of a
    library image or ``"empty"``; ``alt``, an image id to its text per
    language; and ``slots``, an empty list entry as the report names it to the
    id of a library image or ``"remove"`` (``docs/builder.md`` has an
    example)."""
    try:
        stated = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DecisionsInvalid(f"{path}: {exc}") from exc
    if not isinstance(stated, dict):
        raise DecisionsInvalid(
            f"{path}: an object of store schema to decisions"
        )
    return {
        schema: _decisions_of(schema, store) for schema, store in stated.items()
    }


@dataclass(slots=True)
class Resolution:
    """The asset of every old image setting of a store, or why not."""

    #: ``{url as written: the asset it becomes, None for no image}``.
    assets: dict[str, int | None] = field(default_factory=dict)
    #: Each decision that settled a value, to print.
    decided: list[str] = field(default_factory=list)
    #: Each decision about a value no document holds.
    unused: list[str] = field(default_factory=list)
    #: Each value with no asset and no decision, then where it is used.
    problems: list[str] = field(default_factory=list)


def resolve(uses: list[LegacyUse], decisions: Decisions) -> Resolution:
    """What each URL of *uses* becomes. A decision comes first. An image of
    this store is found in the library by its content, and nothing is
    imported: one not there yet is for ``import_builder_media``. Whatever is
    left (an address outside the store, a value that is no image path, a file
    that is gone or that the library refuses) has no asset to become and is a
    problem, named with every place that holds it. So is a decision that
    names an image the library does not hold."""
    schema = connection.schema_name
    hosts = own_hosts()
    decided = decisions.images
    by_url: dict[str, list[LegacyUse]] = defaultdict(list)
    for use in uses:
        by_url[use.url].append(use)
    named = {*decided.values(), *decisions.slots.values()} - {None}
    in_library = set(
        MediaAsset.objects.filter(pk__in=named).values_list("pk", flat=True)
    )
    resolution = Resolution()
    resolution.problems.extend(
        f"{slot} is decided as image {asset}, which is not in the library"
        for slot, asset in sorted(decisions.slots.items())
        if asset is not None and asset not in in_library
    )
    reasons: dict[str, str] = {}
    plan = Plan()
    for url, where in sorted(by_url.items()):
        if url in decided:
            asset = decided[url]
            if asset is not None and asset not in in_library:
                reasons[url] = (
                    f"is decided as image {asset}, which is not in the library"
                )
                continue
            resolution.assets[url] = asset
            became = "no image" if asset is None else f"image {asset}"
            resolution.decided.append(
                f"decision: {url!r} is {became} ({len(where)} use(s))"
            )
            continue
        kind = classify(url, schema, hosts)
        if kind is None:
            reasons[url] = "is no image path"
        elif kind == EXTERNAL:
            reasons[url] = "is an address outside this store, never fetched"
        else:
            plan.found.extend(
                _found(use.document, use.source, use.path, url, kind, hosts)
                for use in where
            )
    resolution.unused.extend(
        f"decision not used: {url!r} is in no document"
        for url in sorted(set(decided) - set(by_url))
    )
    outcomes = run(plan, dry_run=True)
    for import_url, items in plan.internal.items():
        outcome = outcomes[import_url]
        for url in {item.url for item in items}:
            if outcome.status == "failed":
                reasons[url] = f"cannot be imported ({outcome.detail})"
            elif outcome.status == "new":
                reasons[url] = (
                    "is not in the library yet, run import_builder_media "
                    "--all-versions"
                )
            else:
                resolution.assets[url] = outcome.asset_id
    for url, reason in sorted(reasons.items()):
        resolution.problems.append(f"{url!r} {reason}")
        resolution.problems.extend(
            f"    {use.where}: {use.path} ({use.block})" for use in by_url[url]
        )
    return resolution


@dataclass(slots=True)
class AltDecisions:
    """What a migration does to the library's alt text."""

    #: ``{asset id: {language: text}}``, to write.
    fill: dict[int, dict[str, str]] = field(default_factory=dict)
    #: A use that says something else than the asset already does, which wins.
    gives_way: list[str] = field(default_factory=list)
    #: Each decision that settled uses that disagree, to print.
    decided: list[str] = field(default_factory=list)
    #: Each decision about an alt that needed none.
    unused: list[str] = field(default_factory=list)
    #: Uses that disagree, of an asset with nothing written to decide by.
    conflicts: list[str] = field(default_factory=list)


def decide_alts(
    uses: list[LegacyUse],
    assets: Mapping[str, int | None],
    decided: Mapping[tuple[int, str], str],
) -> AltDecisions:
    """Alt text belongs to the image, so one image says one thing in a language.
    What the asset already says wins; a use's text fills a language it has
    none in; and uses that disagree stop the store for someone to choose: by
    writing it on the image, or by a decision (*decided*), whose empty text
    leaves the image without one. Nothing here guesses between them. A use
    that becomes no image has no image to give its alt to."""
    said: dict[tuple[int, str], dict[str, list[LegacyUse]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for use in uses:
        asset = assets[use.url]
        if asset is not None:
            for locale, text in use.alt.items():
                said[asset, locale][text].append(use)
    held = {
        (asset, locale): text
        for asset, locale, text in MediaAsset.translations.field.model.objects.filter(
            master__in={asset for asset, _locale in said}
        ).values_list("master", "language_code", "alt")
        if text
    }
    decisions = AltDecisions()
    settled: set[tuple[int, str]] = set()
    for (asset, locale), texts in sorted(said.items()):
        if (kept := held.get((asset, locale))) is not None:
            decisions.gives_way.extend(
                f"image {asset} [{locale}] keeps {kept!r}; {uses_[0].where} said {text!r}"
                for text, uses_ in texts.items()
                if text != kept
            )
        elif len(texts) == 1:
            decisions.fill.setdefault(asset, {})[locale] = next(iter(texts))
        elif (stated := decided.get((asset, locale))) is not None:
            settled.add((asset, locale))
            if stated:
                decisions.fill.setdefault(asset, {})[locale] = stated
            decisions.decided.append(
                f"decision: image {asset} [{locale}] "
                + (f"says {stated!r}" if stated else "stays without alt")
            )
        else:
            versions = "; ".join(
                f"{text!r} ({uses_[0].where})" for text, uses_ in texts.items()
            )
            decisions.conflicts.append(
                f"image {asset} [{locale}]: its uses disagree and it has no alt "
                f"of its own, so decide it or write one on the image: {versions}"
            )
    decisions.unused.extend(
        f"decision not used: image {asset} [{locale}] has an alt of its own, "
        "or no uses that disagree"
        for asset, locale in sorted(set(decided) - settled)
    )
    return decisions


def _asset_of(value: Any, assets: Mapping[str, int | None]) -> int | None:
    """The asset the entry *value* becomes: none for an empty entry and for a
    value decided to be no image. A value that is no text is refused: its
    setting leaves the document, so it would go without a word. A URL the
    store's read did not find was saved since: the documents are locked only
    after it."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise DocumentRefused(f"{value!r} is no image path")
    if not value.strip():
        return None
    try:
        return assets[value.strip()]
    except KeyError:
        raise DocumentRefused(
            f"{value.strip()!r} was saved after the store's images were read, "
            "run it again"
        ) from None


@dataclass(slots=True)
class SlotDecisions:
    """What was decided for the empty entries of lists (``Decisions.slots``),
    and which of it a run used."""

    decided: Mapping[str, int | None] = field(default_factory=dict)
    #: ``{slot: what was done there}``, to print.
    used: dict[str, str] = field(default_factory=dict)

    @property
    def unused(self) -> list[str]:
        return [
            f"decision not used: {slot} is no empty entry beside an image"
            for slot in sorted(set(self.decided) - set(self.used))
        ]


def _entry_shown(asset: int | None, written: Any) -> str:
    """One entry of an old list as a report shows it."""
    if not isinstance(written, str) or not written.strip():
        return "''"
    became = "no image" if asset is None else f"image {asset}"
    return f"{written.strip()!r} ({became})"


def _place_lists(
    node: dict[str, Any],
    node_id: str,
    node_settings: dict[str, Any],
    old: Mapping[LegacyField, list[tuple[int | None, Any]]],
    where: str,
    decisions: SlotDecisions,
) -> list[str]:
    """Store the lists of one node, and answer the empty entries nobody has
    decided. *old* is each old list as ``(asset, what was written)``, an empty
    entry still in its place.

    The lists of a block may pair by position (the default design draws
    ``mobileArtworks[i]`` as the phone's crop of ``artworks[i]``), and a list
    of references has no empty slot (the storefront's schema takes an asset id
    and nothing else). So no entry may move against another list by itself.
    A position that is empty in every list goes from each, which moves no
    pair. An empty entry beside an image of another list is for a person to
    decide (``slots`` of the decisions): an image to put there, or the position
    taken out of every list of the block, its partner with it.
    """
    lists: dict[str, list[int | None]] = {}
    shown: dict[str, list[str]] = {}
    targets: dict[str, str] = {}
    for legacy in _fields_of(node):
        chosen = node_settings.get(legacy.target)
        if legacy in old:
            name = legacy.source
            lists[name] = [asset for asset, _written in old[legacy]]
            shown[name] = [_entry_shown(*entry) for entry in old[legacy]]
        elif legacy.is_list and isinstance(chosen, list):
            name = legacy.target
            lists[name] = list(chosen)
            shown[name] = [f"image {asset}" for asset in chosen]
        else:
            continue
        targets[name] = legacy.target
    before = {name: list(entries) for name, entries in lists.items()}

    def beside(name: str, position: int) -> list[str]:
        """The other lists that hold an image at *position*."""
        return [
            other
            for other, entries in before.items()
            if other != name
            and position < len(entries)
            and entries[position] is not None
        ]

    removed: set[int] = set()
    undecided: list[str] = []
    for legacy in old:
        name = legacy.source
        for position, asset in enumerate(before[name]):
            if asset is not None:
                continue
            others = beside(name, position)
            if not others:
                removed.add(position)
                continue
            pointer = f"/sections/{node_id}/settings/{name}/{position}"
            slot = f"{where}: {pointer}"
            if slot not in decisions.decided:
                entries = ", ".join(
                    f"/{each}/{position} = {shown[each][position]}"
                    for each in (name, *others)
                )
                undecided.append(
                    f"{pointer} ({node['type']}) is empty: {entries}. Decide "
                    f'an image for it, or "{REMOVE_POSITION}" to take '
                    f"position {position} out of every list of the block"
                )
            elif (image := decisions.decided[slot]) is None:
                removed.add(position)
                decisions.used[slot] = (
                    f"decision: {slot} is removed with its position"
                )
            else:
                lists[name][position] = image
                decisions.used[slot] = f"decision: {slot} is image {image}"
    if undecided:
        return undecided
    for name, entries in lists.items():
        kept = [
            asset
            for position, asset in enumerate(entries)
            if position not in removed
        ]
        if kept:
            node_settings[targets[name]] = kept
        else:
            node_settings.pop(targets[name], None)
    return []


def migrate_document(
    data: Any,
    i18n: Any,
    where: str,
    assets: Mapping[str, int | None],
    slots: SlotDecisions,
) -> tuple[Any, Any, Counter[str]]:
    """Copies of document *data* and its overlay *i18n* with every old image
    setting replaced by the asset it names and its alt taken out of both, and
    how many settings of each block moved. A chosen new setting stays, and the
    old one left beside it goes. A value that became no image (*assets* says
    ``None``) leaves its setting empty. The entries of a list keep their
    positions against the block's other lists (:func:`_place_lists`), by the
    decisions made for this document, which *where* names as a report does
    (``template:home version 3``)."""
    migrated = copy.deepcopy(data)
    overlay = copy.deepcopy(i18n or {})
    moved: Counter[str] = Counter()
    undecided: list[str] = []
    for parent, node_id in list(nodes_of(migrated, "sections")):
        node = parent[node_id]
        node_settings = (
            (node.get("settings") or {}) if isinstance(node, dict) else {}
        )
        old: dict[LegacyField, list[tuple[int | None, Any]]] = {}
        for legacy in _fields_of(node):
            present = legacy.source in node_settings
            value = node_settings.pop(legacy.source, None)
            alt_present = (
                legacy.alt is not None
                and node_settings.pop(legacy.alt, None) is not None
            )
            for per_node in overlay.values():
                translations = (per_node or {}).get(node_id)
                if legacy.alt and translations:
                    alt_present = (
                        translations.pop(f"/{legacy.alt}", None) is not None
                        or alt_present
                    )
            if not (present or alt_present):
                continue
            moved[str(node["type"])] += 1
            if node_settings.get(legacy.target):
                continue
            if legacy.is_list:
                if value is not None and not isinstance(value, list):
                    raise DocumentRefused(
                        f"{node['type']} {node_id!r}: /{legacy.source} is "
                        "no list"
                    )
                old[legacy] = [
                    (_asset_of(url, assets), url) for url in value or []
                ]
            elif (asset := _asset_of(value, assets)) is not None:
                node_settings[legacy.target] = asset
        if old:
            undecided += _place_lists(
                node, node_id, node_settings, old, where, slots
            )
    if undecided:
        raise DocumentRefused(*undecided)
    return migrated, _without_empties(overlay), moved


def _without_empties(overlay: dict[str, Any]) -> dict[str, Any]:
    """*overlay* with a node or a language left with no translation gone."""
    kept: dict[str, Any] = {}
    for locale, per_node in overlay.items():
        nodes = {node: text for node, text in (per_node or {}).items() if text}
        if nodes:
            kept[locale] = nodes
    return kept
