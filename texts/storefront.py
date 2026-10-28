"""The boundary to the storefront: what can be edited, and whether a
store-written text is allowed.

The storefront's locale files are the one source of every text, and its
message compiler the one authority on the syntax, so Django asks it
instead of keeping a second copy of either:

* ``GET  /api/texts/catalogue?schema=<schema>`` - every editable text of
  the build that store renders, with each default's placeholders and a
  ``locked`` flag. The body is immutable per build and its hash is the
  ETag, so a process keeps its copy and revalidates with
  ``If-None-Match``.
* ``POST /api/texts/validate`` - ``{ok: true}`` or a 422
  ``{ok: false, code, detail}``.

The base URL is ``settings.NUXT_INTERNAL_BASE_URL``, the one the cache
purge already uses. Nothing is saved on a guess: when the storefront
cannot answer, ``StorefrontUnavailable`` is raised and the caller refuses.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

#: The codes ``/api/texts/validate`` answers with.
UNKNOWN_KEY = "unknown_key"
LOCKED_KEY = "locked_key"
SYNTAX_ERROR = "syntax_error"
PLACEHOLDER_MISMATCH = "placeholder_mismatch"
REJECTION_CODES = frozenset(
    {UNKNOWN_KEY, LOCKED_KEY, SYNTAX_ERROR, PLACEHOLDER_MISMATCH}
)


class StorefrontUnavailable(Exception):
    """The storefront did not give a usable answer."""


@dataclass(frozen=True, slots=True)
class MessageShape:
    """What a message uses besides its words (``MessageShape`` in the
    storefront's ``shared/types/textCatalogue.ts``)."""

    params: tuple[str, ...]
    plural_cases: int


@dataclass(frozen=True, slots=True)
class CatalogueEntry:
    namespace: str
    key: str
    defaults: dict[str, str]
    placeholders: dict[str, MessageShape]
    locked: bool


@dataclass(frozen=True, slots=True)
class Catalogue:
    hash: str
    locales: tuple[str, ...]
    entries: tuple[CatalogueEntry, ...]
    _index: dict[tuple[str, str], CatalogueEntry] = field(
        default_factory=dict, compare=False, repr=False
    )

    def get(self, namespace: str, key: str) -> CatalogueEntry | None:
        return self._index.get((namespace, key))


@dataclass(frozen=True, slots=True)
class Rejection:
    code: str
    detail: str


def _base_url() -> str:
    base = (settings.NUXT_INTERNAL_BASE_URL or "").rstrip("/")
    if not base:
        raise StorefrontUnavailable("NUXT_INTERNAL_BASE_URL is not set")
    return base


def _parse_catalogue(payload: Any) -> Catalogue:
    try:
        entries = tuple(
            CatalogueEntry(
                namespace=raw["namespace"],
                key=raw["key"],
                defaults=dict(raw.get("defaults") or {}),
                placeholders={
                    locale: MessageShape(
                        params=tuple(shape["params"]),
                        plural_cases=int(shape["pluralCases"]),
                    )
                    for locale, shape in (raw.get("placeholders") or {}).items()
                },
                locked=bool(raw["locked"]),
            )
            for raw in payload["entries"]
        )
        return Catalogue(
            hash=str(payload["hash"]),
            locales=tuple(payload["locales"]),
            entries=entries,
            _index={(e.namespace, e.key): e for e in entries},
        )
    except (KeyError, TypeError, ValueError) as exc:
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
    ``TEXTS_CATALOGUE_REVALIDATE_SECONDS`` have passed, so the hot paths
    cost a dict lookup and a deploy of the storefront is picked up within
    that window.
    """
    now = time.monotonic()
    held = _held.get(schema)
    if (
        held is not None
        and now - held.checked_at < settings.TEXTS_CATALOGUE_REVALIDATE_SECONDS
    ):
        return held.catalogue

    headers = {"Accept": "application/json"}
    if held is not None:
        headers["If-None-Match"] = held.etag
    try:
        response = requests.get(
            f"{_base_url()}/api/texts/catalogue",
            params={"schema": schema},
            headers=headers,
            timeout=settings.TEXTS_STOREFRONT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Text catalogue unavailable: %s", exc)
        raise StorefrontUnavailable(str(exc)) from exc

    if response.status_code == 304 and held is not None:
        held.checked_at = now
        return held.catalogue
    if response.status_code != 200:
        logger.warning("Text catalogue answered %s", response.status_code)
        raise StorefrontUnavailable(
            f"catalogue answered {response.status_code}"
        )
    try:
        catalogue = _parse_catalogue(response.json())
    except ValueError as exc:
        raise StorefrontUnavailable("catalogue is not JSON") from exc
    with _lock:
        _held[schema] = _Held(
            etag=response.headers.get("ETag") or f'"{catalogue.hash}"',
            checked_at=now,
            catalogue=catalogue,
        )
    return catalogue


def validate_text(
    *, namespace: str, key: str, language: str, value: str, schema: str
) -> Rejection | None:
    """None when the storefront accepts *value*, else why it does not."""
    try:
        response = requests.post(
            f"{_base_url()}/api/texts/validate",
            json={
                "namespace": namespace,
                "key": key,
                "locale": language,
                "value": value,
                "schema": schema,
            },
            timeout=settings.TEXTS_STOREFRONT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Text validation unavailable: %s", exc)
        raise StorefrontUnavailable(str(exc)) from exc

    if response.status_code == 200:
        return None
    if response.status_code == 422:
        try:
            body = response.json()
        except ValueError as exc:
            raise StorefrontUnavailable("validation is not JSON") from exc
        code = body.get("code") if isinstance(body, dict) else None
        if code in REJECTION_CODES:
            return Rejection(code=code, detail=str(body.get("detail", "")))
    logger.warning("Text validation answered %s", response.status_code)
    raise StorefrontUnavailable(f"validation answered {response.status_code}")
