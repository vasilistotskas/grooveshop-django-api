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
storefront's own files), ``/uploads/`` or ``/media/<this store>/uploads/``
and ends in an image extension. An absolute ``http(s)`` address with an image
extension is EXTERNAL: it is listed and never fetched. Nothing is rewritten
here: what to put in a document in place of the URL is the block's decision.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from django.db import connection

from builder import media_services, pointers
from builder.errors import BuilderError
from builder.models import BuilderDocument, BuilderVersion, MediaAsset
from builder.services import published_versions

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


def classify(value: str, schema: str) -> str | None:
    """``static``, ``store``, ``external`` or ``None`` (not an image path)."""
    text = value.strip()
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
    text = value.strip()
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
