"""The public read: the current release, resolved for one locale.

A page is the store's theme, its section groups and the page's own
document, taken from the CURRENT release and resolved here so the
storefront never sees another language, a disabled node or an editor
detail: the per-language overlay is merged over the default content and
every disabled section or block is dropped with its children.

Like the texts, this read never calls the storefront and never reads a
draft. The resolved page is cached under the release's PRIMARY KEY, which an
immutable release makes safe for good: a new release reads a new key and
nothing is ever purged. It is the key, not the release NUMBER, because a
number can come back (a store whose builder data was reset starts at 1
again) and a key that comes back serves the content of the release that held
it. The id of the current release is itself kept under the store's cache
generation, which a release moves.

The editor's live preview resolves unsaved working copies through the same
code (``preview``): the documents it is given stand in for the release's
ones of the same keys, and ``assemble`` is the one place a page is put
together, whether its documents come from a release or from the editor.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import connection

from builder import pointers
from builder.models import BuilderRelease, BuilderVersion, DocumentKind
from builder.validation import DOCUMENT_NODE
from tenant.membership import bound_tenant

GROUP_PREFIX = f"{DocumentKind.GROUP.value}:"

#: ``version`` of a document that is not a stored version: the editor's
#: unsaved working copy in a preview.
UNSAVED_VERSION = 0


@dataclass(frozen=True, slots=True)
class Source:
    """What a document is resolved from: its content and overlay, and the
    number of the version they are (``UNSAVED_VERSION`` for none)."""

    data: dict[str, Any]
    i18n: dict[str, Any]
    number: int = UNSAVED_VERSION

    @classmethod
    def of(cls, version: BuilderVersion) -> Source:
        return cls(version.data, version.i18n, version.number)


def _current_id_key(generation: int) -> str:
    return f"builder:{connection.schema_name}:current:{generation}"


def _chrome_cache_key(release_id: int, locale: str) -> str:
    return f"builder:{connection.schema_name}:r{release_id}:chrome:{locale}"


def _page_cache_key(release_id: int, page_key: str, locale: str) -> str:
    return f"builder:{connection.schema_name}:r{release_id}:{page_key}:{locale}"


def current_release_id() -> int:
    """The primary key of the current release, 0 when nothing is released."""
    tenant = bound_tenant()
    key = (
        _current_id_key(tenant.cache_generation) if tenant is not None else None
    )
    if key is not None and (held := cache.get(key)) is not None:
        return held
    top = (
        BuilderRelease.objects.order_by("-number")
        .values_list("pk", flat=True)
        .first()
    )
    release_id = top or 0
    if key is not None:
        cache.set(key, release_id, settings.BUILDER_PUBLISHED_CACHE_SECONDS)
    return release_id


def _overlay_for(i18n: dict[str, Any], locale: str) -> dict[str, Any]:
    if locale == settings.PARLER_DEFAULT_LANGUAGE_CODE:
        return {}
    return i18n.get(locale) or {}


def localised(
    node_settings: dict[str, Any], overrides: dict[str, Any]
) -> dict[str, Any]:
    """A copy of *node_settings* with each overlay pointer's value set."""
    resolved = copy.deepcopy(node_settings)
    for pointer, value in overrides.items():
        path = pointers.resolve_concrete(resolved, pointers.parse(pointer))
        if path is not None:
            pointers.set_at(resolved, path, value)
    return resolved


def _resolve_node(
    node_id: str, node: dict[str, Any], overlay: dict[str, Any]
) -> dict[str, Any] | None:
    if node.get("disabled"):
        return None
    resolved: dict[str, Any] = {
        "type": node["type"],
        "settings": localised(node["settings"], overlay.get(node_id) or {}),
    }
    if "blocks" in node:
        blocks = {
            child_id: child
            for child_id in node["blockOrder"]
            if (
                child := _resolve_node(
                    child_id, node["blocks"][child_id], overlay
                )
            )
            is not None
        }
        resolved["blocks"] = blocks
        resolved["blockOrder"] = list(blocks)
    return resolved


def resolve_document(key: str, source: Source, locale: str) -> dict[str, Any]:
    overlay = _overlay_for(source.i18n, locale)
    sections = {
        section_id: section
        for section_id in source.data["order"]
        if (
            section := _resolve_node(
                section_id, source.data["sections"][section_id], overlay
            )
        )
        is not None
    }
    resolved: dict[str, Any] = {
        "key": key,
        "version": source.number,
        "sections": sections,
        "order": list(sections),
    }
    # A translation of the root settings stands even when the default
    # language set none (``validation._check_root``).
    document_overlay = overlay.get(DOCUMENT_NODE) or {}
    if "settings" in source.data or document_overlay:
        resolved["settings"] = localised(
            source.data.get("settings", {}), document_overlay
        )
    return resolved


def _page_document_key(keys: Iterable[str], page_key: str) -> str | None:
    """The document a page key names: the store's page before the fixed
    page type of the same name."""
    available = set(keys)
    for kind in (DocumentKind.PAGE, DocumentKind.TEMPLATE):
        key = f"{kind.value}:{page_key}"
        if key in available:
            return key
    return None


def _chrome_keys(keys: Iterable[str]) -> set[str]:
    """The documents every page needs: the theme and the section groups."""
    return {
        key
        for key in keys
        if key == DocumentKind.THEME.value or key.startswith(GROUP_PREFIX)
    }


def _sources(release: BuilderRelease, keys: set[str]) -> dict[str, Source]:
    """The release's documents of *keys*, by document key."""
    by_id = {
        release.versions[key]: key for key in keys if key in release.versions
    }
    return {
        by_id[version.pk]: Source.of(version)
        for version in BuilderVersion.objects.filter(pk__in=by_id)
    }


def _resolve_all(
    sources: dict[str, Source], locale: str
) -> dict[str, dict[str, Any]]:
    return {
        key: resolve_document(key, source, locale)
        for key, source in sources.items()
    }


def _chrome(documents: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "theme": documents.get(DocumentKind.THEME.value),
        "groups": {
            key.removeprefix(GROUP_PREFIX): document
            for key, document in sorted(documents.items())
            if key.startswith(GROUP_PREFIX)
        },
    }


def assemble(
    release_number: int,
    locale: str,
    sources: dict[str, Source],
    page_document: str,
) -> dict[str, Any]:
    """A page as the storefront reads it: the chrome among *sources*, and
    the document *page_document*, resolved for *locale*. The one place a
    page is put together, for a release and for the editor's preview."""
    documents = _resolve_all(sources, locale)
    return {
        "release": release_number,
        "locale": locale,
        **_chrome(documents),
        "page": documents[page_document],
    }


def _build(release_id: int, page_key: str, locale: str) -> dict | None:
    release = BuilderRelease.objects.filter(pk=release_id).first()
    if release is None:
        return None
    page_document = _page_document_key(release.versions, page_key)
    if page_document is None:
        return None
    sources = _sources(
        release, _chrome_keys(release.versions) | {page_document}
    )
    return assemble(release.number, locale, sources, page_document)


def preview(
    page_key: str, locale: str, copies: dict[str, Source]
) -> dict[str, Any] | None:
    """The page as it would read if *copies* (unsaved working copies, by
    document key) were released now: each stands in for the current
    release's document of its key, and everything else is the release's.
    ``None`` when neither the copies nor the release have the page. Reads
    and stores nothing that outlives the call, and is never cached."""
    release = BuilderRelease.objects.order_by("-number").first()
    versions = release.versions if release is not None else {}
    page_document = _page_document_key({*versions, *copies}, page_key)
    if page_document is None:
        return None
    wanted = _chrome_keys({*versions, *copies}) | {page_document}
    sources = (
        _sources(release, wanted - copies.keys()) if release is not None else {}
    )
    sources.update({key: copies[key] for key in wanted & copies.keys()})
    return assemble(
        release.number if release is not None else 0,
        locale,
        sources,
        page_document,
    )


def published_page(page_key: str, locale: str) -> dict[str, Any] | None:
    """The page as the current release has it, or ``None`` when the
    release has neither a page nor a template of that name."""
    release_id = current_release_id()
    if release_id == 0:
        return None
    key = _page_cache_key(release_id, page_key, locale)
    if (held := cache.get(key)) is not None:
        return held
    built = _build(release_id, page_key, locale)
    if built is not None:
        cache.set(key, built, settings.BUILDER_PUBLISHED_CACHE_SECONDS)
    return built


def _build_index(release_id: int, locale: str) -> dict[str, Any] | None:
    release = BuilderRelease.objects.filter(pk=release_id).first()
    if release is None:
        return None
    pages = {
        key: version_id
        for key, version_id in release.versions.items()
        if key.partition(":")[0] in {DocumentKind.PAGE, DocumentKind.TEMPLATE}
    }
    created = dict(
        BuilderVersion.objects.filter(pk__in=pages.values()).values_list(
            "pk", "created_at"
        )
    )
    return {
        "release": release.number,
        "locale": locale,
        **_chrome(
            _resolve_all(
                _sources(release, _chrome_keys(release.versions)), locale
            )
        ),
        "pages": [
            {
                "key": key.partition(":")[2],
                "kind": key.partition(":")[0],
                "updatedAt": created[version_id].isoformat(),
            }
            for key, version_id in sorted(pages.items())
        ],
    }


def published_index(locale: str) -> dict[str, Any]:
    """What every page of the store needs and no page owns: the theme, the
    section groups (the chrome), and which pages and templates the current
    release has, with when each last changed (the sitemap's ``lastmod``).

    A store with no release is a valid, empty answer, not a 404: the chrome
    and the sitemap render from nothing."""
    release_id = current_release_id()
    if release_id == 0:
        return {
            "release": 0,
            "locale": locale,
            "theme": None,
            "groups": {},
            "pages": [],
        }
    key = _chrome_cache_key(release_id, locale)
    if (held := cache.get(key)) is not None:
        return held
    built = _build_index(release_id, locale)
    assert built is not None  # the id came from a release row
    cache.set(key, built, settings.BUILDER_PUBLISHED_CACHE_SECONDS)
    return built
