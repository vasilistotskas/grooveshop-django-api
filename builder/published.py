"""The public read: the current release, resolved for one locale.

A page is the store's theme, its section groups and the page's own
document, taken from the CURRENT release and resolved here so the
storefront never sees another language, a disabled node or an editor
detail: the per-language overlay is merged over the default content and
every disabled section or block is dropped with its children.

Like the texts, this read never calls the storefront and never reads a
draft. The resolved page is cached under the RELEASE number, which an
immutable release makes safe for good: a new release reads a new key and
nothing is ever purged. The number of the current release is itself kept
under the store's cache generation, which a release moves.
"""

from __future__ import annotations

import copy
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import connection

from builder import pointers
from builder.models import BuilderRelease, BuilderVersion, DocumentKind
from builder.validation import DOCUMENT_NODE
from tenant.membership import bound_tenant

GROUP_PREFIX = f"{DocumentKind.GROUP.value}:"


def _current_number_key(generation: int) -> str:
    return f"builder:{connection.schema_name}:current:{generation}"


def _chrome_cache_key(release: int, locale: str) -> str:
    return f"builder:{connection.schema_name}:r{release}:chrome:{locale}"


def _page_cache_key(release: int, page_key: str, locale: str) -> str:
    return f"builder:{connection.schema_name}:r{release}:{page_key}:{locale}"


def current_release_number() -> int:
    """The number of the current release, 0 when nothing is released."""
    tenant = bound_tenant()
    key = (
        _current_number_key(tenant.cache_generation)
        if tenant is not None
        else None
    )
    if key is not None and (held := cache.get(key)) is not None:
        return held
    top = (
        BuilderRelease.objects.order_by("-number")
        .values_list("number", flat=True)
        .first()
    )
    number = top or 0
    if key is not None:
        cache.set(key, number, settings.BUILDER_PUBLISHED_CACHE_SECONDS)
    return number


def _overlay_for(i18n: dict[str, Any], locale: str) -> dict[str, Any]:
    if locale == settings.PARLER_DEFAULT_LANGUAGE_CODE:
        return {}
    return i18n.get(locale) or {}


def _localised(
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
        "settings": _localised(node["settings"], overlay.get(node_id) or {}),
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


def resolve_document(
    key: str, version: BuilderVersion, locale: str
) -> dict[str, Any]:
    overlay = _overlay_for(version.i18n, locale)
    sections = {
        section_id: section
        for section_id in version.data["order"]
        if (
            section := _resolve_node(
                section_id, version.data["sections"][section_id], overlay
            )
        )
        is not None
    }
    resolved: dict[str, Any] = {
        "key": key,
        "version": version.number,
        "sections": sections,
        "order": list(sections),
    }
    if "settings" in version.data:
        resolved["settings"] = _localised(
            version.data["settings"], overlay.get(DOCUMENT_NODE) or {}
        )
    return resolved


def _page_document_key(versions: dict[str, int], page_key: str) -> str | None:
    for kind in (DocumentKind.PAGE, DocumentKind.TEMPLATE):
        key = f"{kind.value}:{page_key}"
        if key in versions:
            return key
    return None


def _documents(
    release: BuilderRelease, locale: str, *, with_keys: set[str]
) -> dict[str, dict[str, Any]]:
    """The release's theme and groups, and the documents named in
    *with_keys*, resolved for *locale*, by document key."""
    wanted = {
        key
        for key in release.versions
        if key == DocumentKind.THEME.value
        or key.startswith(GROUP_PREFIX)
        or key in with_keys
    }
    by_id = {release.versions[key]: key for key in wanted}
    return {
        by_id[version.pk]: resolve_document(by_id[version.pk], version, locale)
        for version in BuilderVersion.objects.filter(pk__in=by_id)
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


def _build(release_number: int, page_key: str, locale: str) -> dict | None:
    release = BuilderRelease.objects.filter(number=release_number).first()
    if release is None:
        return None
    page_document = _page_document_key(release.versions, page_key)
    if page_document is None:
        return None
    documents = _documents(release, locale, with_keys={page_document})
    return {
        "release": release.number,
        "locale": locale,
        **_chrome(documents),
        "page": documents[page_document],
    }


def published_page(page_key: str, locale: str) -> dict[str, Any] | None:
    """The page as the current release has it, or ``None`` when the
    release has neither a page nor a template of that name."""
    number = current_release_number()
    if number == 0:
        return None
    key = _page_cache_key(number, page_key, locale)
    if (held := cache.get(key)) is not None:
        return held
    built = _build(number, page_key, locale)
    if built is not None:
        cache.set(key, built, settings.BUILDER_PUBLISHED_CACHE_SECONDS)
    return built


def _build_index(release_number: int, locale: str) -> dict[str, Any] | None:
    release = BuilderRelease.objects.filter(number=release_number).first()
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
        **_chrome(_documents(release, locale, with_keys=set())),
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
    number = current_release_number()
    if number == 0:
        return {
            "release": 0,
            "locale": locale,
            "theme": None,
            "groups": {},
            "pages": [],
        }
    key = _chrome_cache_key(number, locale)
    if (held := cache.get(key)) is not None:
        return held
    built = _build_index(number, locale)
    assert built is not None  # the number came from a release row
    cache.set(key, built, settings.BUILDER_PUBLISHED_CACHE_SECONDS)
    return built
