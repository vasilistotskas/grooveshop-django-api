"""Write-side validation for ``Tenant.theme_metadata``.

Plain-Python mirror of the storefront's render-time schema
(``shared/theme/metadataSchema.ts`` in the Nuxt repo) — keep the two in
sync when adding tokens. The Nuxt parse remains the render-time
authority (bad historical data degrades to preset values there); this
validator stops NEW bad data at the admin/API boundary with a readable
error instead.
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django_tenants.utils import get_public_schema_name

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

_RADIUS_ALLOWLIST = {
    "0",
    "0.125rem",
    "0.25rem",
    "0.375rem",
    "0.5rem",
    "0.625rem",
}

_FONT_ALLOWLIST = {
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
}

_CONTAINER_ALLOWLIST = {"narrow", "default", "wide"}

_SHADES = {
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
}

_SCALE_KEYS = {"primaryScale", "neutralScale"}


def _validate_scale(name: str, scale: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(scale, dict):
        return [f"colors.{name} must be an object of shade -> hex"]
    for shade, value in scale.items():
        if str(shade) not in _SHADES:
            errors.append(f"colors.{name}.{shade}: unknown shade")
        elif not isinstance(value, str) or not _HEX_RE.match(value):
            errors.append(
                f"colors.{name}.{shade}: must be a #RRGGBB hex string"
            )
    return errors


def validate_theme_metadata(value: object) -> None:
    """JSONField validator for ``Tenant.theme_metadata``."""
    if value in (None, {}):
        return
    if not isinstance(value, dict):
        raise ValidationError(_("theme_metadata must be a JSON object."))

    errors: list[str] = []
    for key, entry in value.items():
        if key == "radius":
            if entry not in _RADIUS_ALLOWLIST:
                errors.append(
                    f"radius: must be one of {sorted(_RADIUS_ALLOWLIST)}"
                )
        elif key == "fontSans":
            if entry not in _FONT_ALLOWLIST:
                errors.append(
                    f"fontSans: must be one of {sorted(_FONT_ALLOWLIST)}"
                )
        elif key == "container":
            if entry not in _CONTAINER_ALLOWLIST:
                errors.append(
                    f"container: must be one of {sorted(_CONTAINER_ALLOWLIST)}"
                )
        elif key == "colors":
            if not isinstance(entry, dict):
                errors.append("colors: must be an object")
                continue
            for scale_key, scale in entry.items():
                if scale_key not in _SCALE_KEYS:
                    errors.append(f"colors.{scale_key}: unknown key")
                else:
                    errors.extend(_validate_scale(str(scale_key), scale))
        else:
            errors.append(f"{key}: unknown key")

    if errors:
        raise ValidationError(
            _("Invalid theme_metadata: %(details)s"),
            params={"details": "; ".join(errors)},
        )


# ---------------------------------------------------------------------------
# Reserved schema names
# ---------------------------------------------------------------------------

# django-tenants' own ``_check_schema_name`` field validator only checks
# that the value is a syntactically valid PostgreSQL identifier — it does
# NOT reject names that collide with special meanings elsewhere in this
# codebase. Layered defense-in-depth on top of that check:
#
# * ``public`` / ``information_schema`` — reserved by Postgres itself
#   (``information_schema``) or by django-tenants (``public`` is the
#   shared schema; ``TenantMixin.save()`` would still create a
#   colliding schema for a tenant literally named "public").
# * ``global`` — special-cased by ``tenant/cache.py``'s tenant-scoped
#   cache-key function as the platform-wide cache namespace; a tenant
#   schema named "global" would silently share cache keys with the
#   platform routines instead of getting its own isolated namespace.
# * names starting with ``pg_`` — reserved prefix for PostgreSQL system
#   schemas (``pg_catalog``, ``pg_toast``, …); Postgres itself rejects
#   ``CREATE SCHEMA pg_*`` for a non-superuser, but failing here gives
#   a much clearer error than a raw DB exception mid schema-creation.
# * the media-stream image proxy's historic reserved-word list
#   (``data``, ``file``, ``ftp``, ``about``, ``javascript``,
#   ``vbscript``, ``expression``, ``eval``) — carried over here so a
#   tenant schema name can never collide with a sanitizer edge case on
#   that service.
RESERVED_SCHEMA_NAMES = frozenset(
    {
        "public",
        "global",
        "information_schema",
        # media-stream sanitizer's former reserved words
        "data",
        "file",
        "ftp",
        "about",
        "javascript",
        "vbscript",
        "expression",
        "eval",
    }
)


def validate_reserved_schema_name(value: str) -> None:
    """Field validator for ``Tenant.schema_name`` — reject reserved names.

    Case-insensitive: ``Public`` / ``PUBLIC`` are rejected exactly like
    ``public`` since PostgreSQL schema names are effectively
    case-sensitive but a tenant named for confusion is still a footgun.

    Carve-out: the literal ``get_public_schema_name()`` value (``public``
    by default) is exempt. The framework *requires* exactly one Tenant
    row with that schema_name (see ``bootstrap_platform``) — without
    this carve-out, ``full_clean()``/``ModelForm`` validation (e.g.
    editing that row in ``TenantAdmin`` on the public-schema host) would
    reject the one legitimate use of the name. ``schema_name`` is
    ``unique=True`` so this cannot be abused to create a second row
    claiming the public schema.
    """
    if not value:
        return
    normalized = value.strip().lower()
    if normalized == get_public_schema_name().strip().lower():
        return
    if normalized in RESERVED_SCHEMA_NAMES or normalized.startswith("pg_"):
        raise ValidationError(
            _(
                "'%(value)s' is a reserved schema name and cannot be "
                "used for a tenant."
            ),
            params={"value": value},
        )
