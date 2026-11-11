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
image extension. An absolute ``http(s)`` address with an image extension is
EXTERNAL: it is listed and never fetched.

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

from django.conf import settings
from django.db import connection

from builder import media_services, pointers
from builder.block_upgrades import nodes_of
from builder.document_rewrite import DocumentRefused
from builder.errors import BuilderError
from builder.models import BuilderDocument, BuilderVersion, MediaAsset
from builder.services import published_versions
from builder.validation import VERSIONED_TYPE

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
                grouped[import_url_of(item.url)].append(item)
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


def classify(value: str, schema: str) -> str | None:
    """``static``, ``store``, ``external`` or ``None`` (not an image path)."""
    text = _rooted(value.strip())
    if not text or len(text) > 1000 or not _has_image_extension(text):
        return None
    if _ABSOLUTE.match(text):
        return (
            EXTERNAL
            if text.lower().startswith(("http:", "https:", "//"))
            else None
        )
    if text.startswith("/img/"):
        return STATIC
    if text.startswith(("/uploads/", f"/media/{schema}/uploads/")):
        return STORE
    if text.startswith("/media/"):
        # Another store's file: not ours to copy.
        return EXTERNAL
    return None


def import_url_of(value: str) -> str:
    """The path ``media_services`` reads: ``/uploads/x`` is
    ``/media/<store>/uploads/x``."""
    text = _rooted(value.strip())
    if text.startswith("/uploads/"):
        return f"/media/{connection.schema_name}{text}"
    return text


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


def scan(*, all_versions: bool = False) -> Plan:
    """Every image string in the drafts and the published versions of this
    store (every version with ``all_versions``)."""
    schema = connection.schema_name
    plan = Plan()

    def collect(document: str, source: str, data: Any, prefix: str) -> None:
        for path, value in _strings(data):
            kind = classify(value, schema)
            if kind is not None:
                plan.found.append(
                    Found(document, source, prefix + path, value.strip(), kind)
                )

    for doc in BuilderDocument.objects.order_by("key"):
        collect(doc.key, "draft", doc.draft, "")
        collect(doc.key, "draft", doc.draft_i18n, "/i18n")
    versions = BuilderVersion.objects.select_related("document").order_by(
        "document__key", "number"
    )
    if not all_versions:
        versions = versions.filter(pk__in=published_versions().values())
    for version in versions:
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
    for document in BuilderDocument.objects.order_by("key"):
        current.extend(
            legacy_uses(
                document.key, "draft", document.draft, document.draft_i18n
            )
        )
    live = set(published_versions().values())
    for version in BuilderVersion.objects.select_related("document").order_by(
        "document__key", "number"
    ):
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


class DecisionsInvalid(ValueError):
    """The decisions file does not say what it must."""


@dataclass(frozen=True, slots=True)
class Decisions:
    """What an operator decided for one store, value by value. Nothing else
    settles a value the library cannot take or an alt its uses disagree on."""

    #: ``{url as a document writes it: the library image, None for none}``.
    images: Mapping[str, int | None] = field(default_factory=dict)
    #: ``{(asset id, language): the image's alt, empty for none}``.
    alts: Mapping[tuple[int, str], str] = field(default_factory=dict)


def _decisions_of(schema: str, stated: Any) -> Decisions:
    def bad(what: str) -> DecisionsInvalid:
        return DecisionsInvalid(f"{schema}: {what}")

    if not isinstance(stated, dict) or set(stated) - {"images", "alt"}:
        raise bad('a store holds "images" and "alt", nothing else')
    stated_images = stated.get("images", {})
    stated_alts = stated.get("alt", {})
    if not isinstance(stated_images, dict) or not isinstance(stated_alts, dict):
        raise bad('"images" and "alt" are objects')
    images: dict[str, int | None] = {}
    for url, asset in stated_images.items():
        if asset == NO_IMAGE:
            images[url.strip()] = None
        elif type(asset) is int and asset > 0:
            images[url.strip()] = asset
        else:
            raise bad(
                f'images[{url!r}] is an image id of the library or "{NO_IMAGE}"'
            )
    alts: dict[tuple[int, str], str] = {}
    for asset, texts in stated_alts.items():
        if not asset.isdecimal() or not isinstance(texts, dict):
            raise bad(f"alt[{asset!r}] is an image id to {{language: text}}")
        for locale, alt in texts.items():
            if not isinstance(alt, str):
                raise bad(f"alt[{asset!r}][{locale!r}] is a text")
            alts[int(asset), locale] = alt.strip()
    return Decisions(images, alts)


def read_decisions(path: Path) -> dict[str, Decisions]:
    """``{store schema: its decisions}`` of the file an operator wrote. A
    store holds ``images``, each value as a document writes it to the id of a
    library image or ``"empty"``, and ``alt``, an image id to its text per
    language (``docs/builder.md`` has an example)."""
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
    #: Each value with no asset and no decision, then where it is used.
    problems: list[str] = field(default_factory=list)


def resolve(
    uses: list[LegacyUse], decided: Mapping[str, int | None]
) -> Resolution:
    """What each URL of *uses* becomes. A decision comes first. An image of
    this store is found in the library by its content, and nothing is
    imported: one not there yet is for ``import_builder_media``. Whatever is
    left (an address outside the store, a value that is no image path, a file
    that is gone or that the library refuses) has no asset to become and is a
    problem, named with every place that holds it."""
    schema = connection.schema_name
    by_url: dict[str, list[LegacyUse]] = defaultdict(list)
    for use in uses:
        by_url[use.url].append(use)
    in_library = set(
        MediaAsset.objects.filter(
            pk__in={asset for asset in decided.values() if asset is not None}
        ).values_list("pk", flat=True)
    )
    resolution = Resolution()
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
        kind = classify(url, schema)
        if kind is None:
            reasons[url] = "is no image path"
        elif kind == EXTERNAL:
            reasons[url] = "is an address outside this store, never fetched"
        else:
            plan.found.extend(
                Found(use.document, use.source, use.path, url, kind)
                for use in where
            )
    resolution.decided.extend(
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
            f"    {use.where} {use.path} ({use.block})" for use in by_url[url]
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
    decisions.decided.extend(
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


def _place_lists(
    node: dict[str, Any],
    node_id: str,
    node_settings: dict[str, Any],
    slots: Mapping[LegacyField, list[int | None]],
) -> None:
    """Store the lists of one node: *slots* is each old list as assets, an
    empty entry still in its place.

    The lists of a block pair by position (``hero_carousel`` draws
    ``mobileArtworks[i]`` as the phone's crop of ``artworks[i]``), and a list
    of references has no empty slot (the storefront's schema takes an asset id
    and nothing else). So no entry may move against another list. A position
    that is empty in every list goes from each, which moves no pair. An empty
    entry beside an image of another list can be neither kept nor dropped,
    and is refused.
    """
    every: dict[LegacyField, list[Any]] = dict(slots)
    for legacy in _fields_of(node):
        chosen = node_settings.get(legacy.target)
        if legacy.is_list and legacy not in slots and isinstance(chosen, list):
            every[legacy] = chosen

    def holders(position: int) -> list[str]:
        """The settings that hold an image at *position*."""
        return [
            legacy.source if legacy in slots else legacy.target
            for legacy, entries in every.items()
            if position < len(entries) and entries[position] is not None
        ]

    for legacy, entries in slots.items():
        kept: list[int] = []
        for position, asset in enumerate(entries):
            if asset is not None:
                kept.append(asset)
            elif others := holders(position):
                raise DocumentRefused(
                    f"{node['type']} {node_id!r}: /{legacy.source}/{position} "
                    f"is empty and /{others[0]}/{position} is not; "
                    "the lists pair by position and a list of images has no "
                    "empty slot"
                )
        if kept:
            node_settings[legacy.target] = kept


def migrate_document(
    data: Any, i18n: Any, assets: Mapping[str, int | None]
) -> tuple[Any, Any, Counter[str]]:
    """Copies of document *data* and its overlay *i18n* with every old image
    setting replaced by the asset it names and its alt taken out of both, and
    how many settings of each block moved. A chosen new setting stays, and the
    old one left beside it goes. A value that became no image (*assets* says
    ``None``) leaves its setting empty. The entries of a list keep their
    positions against the block's other lists (:func:`_place_lists`)."""
    migrated = copy.deepcopy(data)
    overlay = copy.deepcopy(i18n or {})
    moved: Counter[str] = Counter()
    for parent, node_id in list(nodes_of(migrated, "sections")):
        node = parent[node_id]
        node_settings = (
            (node.get("settings") or {}) if isinstance(node, dict) else {}
        )
        slots: dict[LegacyField, list[int | None]] = {}
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
                slots[legacy] = [_asset_of(url, assets) for url in value or []]
            elif (asset := _asset_of(value, assets)) is not None:
                node_settings[legacy.target] = asset
        if slots:
            _place_lists(node, node_id, node_settings, slots)
    return migrated, _without_empties(overlay), moved


def _without_empties(overlay: dict[str, Any]) -> dict[str, Any]:
    """*overlay* with a node or a language left with no translation gone."""
    kept: dict[str, Any] = {}
    for locale, per_node in overlay.items():
        nodes = {node: text for node, text in (per_node or {}).items() if text}
        if nodes:
            kept[locale] = nodes
    return kept
