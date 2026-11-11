"""JSON Schemas for the tenant's structured JSON fields
(``core.json_schema``)."""

from __future__ import annotations

from typing import Any

from core.utils.tenant_urls import STOREFRONT_LOCALES


def available_locales() -> dict[str, Any]:
    """Locales the storefront has routes for (``STOREFRONT_LOCALES``,
    kept apart from ``settings.LANGUAGES``, which names the content
    languages). Empty = single-language on
    ``default_locale``; that it contains ``default_locale`` otherwise
    is a cross-field rule (``Tenant.clean``)."""
    return {
        "type": "array",
        "items": {"enum": list(STOREFRONT_LOCALES)},
        "uniqueItems": True,
    }


def store_description_i18n() -> dict[str, Any]:
    """``Tenant.store_description_i18n``: the store line per locale.

    ``store_description`` is the default locale's wording; this maps the
    OTHER storefront locales to theirs. The schema admits every
    ``STOREFRONT_LOCALES`` code (one editor row per locale); that the
    tenant's own default locale is absent is a cross-field rule
    (``Tenant.clean``), since the default is per tenant."""
    return {
        "type": "object",
        "properties": {
            code: {"type": "string", "title": code}
            for code in STOREFRONT_LOCALES
        },
        "additionalProperties": False,
    }


def allowed_csp_sources() -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "string",
            "pattern": "^(https://|http://localhost|wss://)",
        },
    }
