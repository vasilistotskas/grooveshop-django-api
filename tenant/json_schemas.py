"""JSON Schemas for the tenant's structured JSON fields
(``core.json_schema``).

``theme_metadata`` mirrors the storefront's render-time schema
(``shared/theme/metadataSchema.ts`` in the Nuxt repo): the Nuxt parse
stays the render-time authority (bad historical data degrades to preset
values there), this stops new bad data at the admin and API boundary.
"""

from __future__ import annotations

from typing import Any

from core.utils.tenant_urls import STOREFRONT_LOCALES

_HEX = {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}

_RADII = ["0", "0.125rem", "0.25rem", "0.375rem", "0.5rem", "0.625rem"]

_FONTS = [
    "system",
    "inter",
    "roboto",
    "open-sans",
    "lato",
    "poppins",
    "montserrat",
    "noto-sans",
    "raleway",
    "nunito-sans",
    "manrope",
    "playfair-display",
    "source-serif-4",
    # Engineered/technical pairing — both ship a Greek subset, which
    # most technical faces do not (IBM Plex Mono and Plex Condensed
    # have none). Mirrored in shared/theme/constants.ts.
    "ibm-plex-sans",
    "jetbrains-mono",
]

_CONTAINERS = ["narrow", "default", "wide"]

_SHADES = [
    "50",
    "100",
    "200",
    "300",
    "400",
    "500",
    "600",
    "700",
    "800",
    "900",
    "950",
]

_SCALES = ["primaryScale", "neutralScale", "secondaryScale"]


def _scale_set() -> dict[str, Any]:
    """``colors`` / ``darkColors``: scale name -> shade -> hex."""
    scale = {
        "type": "object",
        "properties": {shade: _HEX for shade in _SHADES},
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {name: scale for name in _SCALES},
        "additionalProperties": False,
    }


def theme_metadata() -> dict[str, Any]:
    font = {"enum": _FONTS}
    return {
        "type": "object",
        "properties": {
            "radius": {"enum": _RADII},
            "fontSans": font,
            "fontDisplay": font,
            "fontMono": font,
            "container": {"enum": _CONTAINERS},
            "colors": _scale_set(),
            "darkColors": _scale_set(),
            "likedHex": _HEX,
            "accentDarkHex": _HEX,
        },
        "additionalProperties": False,
    }


def available_locales() -> dict[str, Any]:
    """Locales the storefront has routes for (``STOREFRONT_LOCALES``,
    narrower than ``settings.LANGUAGES``: Django carries ``de`` content,
    the storefront has no ``/de``). Empty = single-language on
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
