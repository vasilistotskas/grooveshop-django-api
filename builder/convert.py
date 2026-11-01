"""The storefront's converter from legacy page_config data to documents.

``POST /api/builder/convert-legacy`` is the one implementation of
"props become settings and blocks" (the storefront's blocks know their own
settings), so Django never converts: it sends a layout or a menu as
``builder.legacy`` describes it and validates what comes back like any
other document. The call is authenticated with the shared secret the cache
purge already uses (``NUXT_CACHE_PURGE_TOKEN``) and is deterministic: the
same request gives the same bytes, which is what makes the import
repeatable.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests
from django.conf import settings
from django.db import connection

from builder.legacy import default_locale
from core.storefront import StorefrontUnavailable, base_url

logger = logging.getLogger(__name__)

TOKEN_HEADER = "X-Cache-Purge-Token"


@dataclass(frozen=True, slots=True)
class Converted:
    """A converted document: what to store and what to tell the operator."""

    document: dict[str, Any]
    i18n: dict[str, Any]
    warnings: list[dict[str, str]] = field(default_factory=list)


class ConversionRefused(Exception):
    """The storefront cannot convert this input (unknown block type, props
    its schema refuses): a fact about the data, not an outage."""

    def __init__(self, errors: list[dict[str, str]]) -> None:
        super().__init__(errors)
        self.errors = errors


def convert_legacy(body: dict[str, Any]) -> Converted:
    token = settings.NUXT_CACHE_PURGE_TOKEN
    if not token:
        raise StorefrontUnavailable("NUXT_CACHE_PURGE_TOKEN is not set")
    request = {
        "schema": connection.schema_name,
        "defaultLocale": default_locale(),
        **body,
    }
    try:
        response = requests.post(
            f"{base_url()}/api/builder/convert-legacy",
            json=request,
            headers={TOKEN_HEADER: token},
            timeout=settings.BUILDER_STOREFRONT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        logger.warning("Legacy conversion unavailable: %s", exc)
        raise StorefrontUnavailable(str(exc)) from exc

    if response.status_code == 200:
        try:
            payload = response.json()
            return Converted(
                document=payload["document"],
                i18n=payload.get("i18n") or {},
                warnings=payload.get("warnings") or [],
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise StorefrontUnavailable("unexpected conversion shape") from exc
    if response.status_code == 422:
        try:
            errors = response.json().get("errors") or []
        except ValueError as exc:
            raise StorefrontUnavailable(
                "conversion refusal is not JSON"
            ) from exc
        raise ConversionRefused(errors)
    raise StorefrontUnavailable(f"conversion answered {response.status_code}")
