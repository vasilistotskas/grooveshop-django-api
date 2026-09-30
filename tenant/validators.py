"""Validators for tenant settings (``django-extra-settings``) and the
schema name. The tenant's structured JSON fields validate against JSON
Schemas instead (``tenant.json_schemas``)."""

from __future__ import annotations

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from django_tenants.utils import get_public_schema_name

# ---------------------------------------------------------------------------
# BUSINESS_HOURS extra_setting
# ---------------------------------------------------------------------------

_DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def validate_business_hours_setting(value: object) -> bool:
    """django-extra-settings validator for ``BUSINESS_HOURS``.

    extra_settings validators return a BOOLEAN (they don't raise) —
    ``Setting.validate()`` wraps a falsy result in its own
    ``ValidationError``. Mirrored render-side by ``zBusinessHours``
    (``shared/schemas/businessHours.ts`` in the Nuxt repo) — keep the
    two in sync.

    Shape: ``{"timezone": "<IANA zone>", "schedule": {"mon".."sun":
    null | {"opens": "HH:MM", "closes": "HH:MM"}}}``. All seven day
    keys required; ``null`` = closed. Empty value = feature unset.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if value in (None, "", {}):
        return True
    if not isinstance(value, dict):
        return False
    data: dict[str, object] = {str(k): v for k, v in value.items()}
    if set(data) != {"timezone", "schedule"}:
        return False

    try:
        ZoneInfo(str(data["timezone"]))
    except ZoneInfoNotFoundError, ValueError, TypeError:
        return False

    raw_schedule = data["schedule"]
    if not isinstance(raw_schedule, dict):
        return False
    schedule: dict[str, object] = {str(k): v for k, v in raw_schedule.items()}
    if set(schedule) != set(_DAY_KEYS):
        return False
    for raw_entry in schedule.values():
        if raw_entry is None:
            continue
        if not isinstance(raw_entry, dict):
            return False
        entry: dict[str, object] = {str(k): v for k, v in raw_entry.items()}
        if set(entry) != {"opens", "closes"}:
            return False
        opens, closes = entry["opens"], entry["closes"]
        if not isinstance(opens, str) or not _TIME_RE.match(opens):
            return False
        if not isinstance(closes, str) or not _TIME_RE.match(closes):
            return False
        # Zero-padded HH:MM compares correctly as strings.
        if opens >= closes:
            return False
    return True


# ---------------------------------------------------------------------------
# STORE_OFFICES extra_setting
# ---------------------------------------------------------------------------

# ``role`` is the short word a design prints BESIDE an office —
# "ΕΔΡΑ" / "ΓΡΑΦΕΙΟ", "Head office" / "Branch". A text key, so the
# i18n overlay translates it like the rest.
_OFFICE_TEXT_KEYS = ("label", "street", "area", "city", "role")
_OFFICE_KEYS = {*_OFFICE_TEXT_KEYS, "postal", "phones", "i18n"}


def validate_store_offices_setting(value: object) -> bool:
    """django-extra-settings validator for ``STORE_OFFICES``.

    Returns a BOOLEAN, like every extra_settings validator —
    ``Setting.validate()`` wraps a falsy result in its own
    ``ValidationError``.

    Shape: a list of at most 10 offices, each
    ``{"label", "street", "area", "postal", "city", "role",
    "phones": [...], "i18n": {"<locale>": {"label"?, "street"?,
    "area"?, "city"?, "role"?}}}``. ``label`` and ``street`` are
    required; the rest are optional.

    ``i18n`` is a PARTIAL per-locale override of the text keys only,
    the same shape and the same reasoning as ``PageSection.i18n``: a
    postcode and a phone number are the same in every language, so
    duplicating them per locale would only invite drift. The default
    locale is not a valid key — those values ARE the entry's own.
    """
    if value in (None, "", []):
        return True
    if not isinstance(value, list) or len(value) > 10:
        return False

    from core.utils.i18n import available_language_codes

    codes = available_language_codes()
    default = settings.PARLER_DEFAULT_LANGUAGE_CODE

    for raw in value:
        if not isinstance(raw, dict):
            return False
        office = {str(k): v for k, v in raw.items()}
        if set(office) - _OFFICE_KEYS:
            return False
        for key in ("label", "street"):
            if not isinstance(office.get(key), str) or not office[key].strip():
                return False
        for key in ("area", "postal", "city", "role"):
            entry = office.get(key)
            if entry is not None and not isinstance(entry, str):
                return False
        phones = office.get("phones", [])
        if not isinstance(phones, list) or len(phones) > 6:
            return False
        if any(not isinstance(p, str) or not p.strip() for p in phones):
            return False

        i18n = office.get("i18n", {})
        if not isinstance(i18n, dict):
            return False
        for code, override in i18n.items():
            if code not in codes or code == default:
                return False
            if not isinstance(override, dict):
                return False
            if set(override) - set(_OFFICE_TEXT_KEYS):
                return False
            if any(not isinstance(v, str) for v in override.values()):
                return False

    return True


# ---------------------------------------------------------------------------
# SOCIAL_LOGIN_PROVIDERS extra_setting
# ---------------------------------------------------------------------------

_PROVIDER_ID_RE = re.compile(r"^[a-z0-9_-]+$")


def validate_social_login_providers_setting(value: object) -> bool:
    """django-extra-settings validator (boolean contract, like
    ``validate_business_hours_setting``).

    A list of provider ids, each ``"*"`` (= all configured providers)
    or a lowercase slug (``google``, ``facebook``, …). Empty list =
    social login off. Enforced by
    ``TenantSocialAccountAdapter.list_apps``.
    """
    if value in (None, ""):
        return True
    if not isinstance(value, list):
        return False
    return all(
        isinstance(item, str) and (item == "*" or _PROVIDER_ID_RE.match(item))
        for item in value
    )


# ---------------------------------------------------------------------------
# ANNOUNCEMENT_BAR extra_setting
# ---------------------------------------------------------------------------

_ANNOUNCEMENT_KEYS = {
    "enabled",
    "text",
    "i18n",
    "link",
    "icon",
    "color",
    "dismissible",
    "id",
}
_ANNOUNCEMENT_COLORS = {
    "primary",
    "secondary",
    "neutral",
    "info",
    "success",
    "warning",
    "error",
}
_ICON_NAME_RE = re.compile(r"^i-[a-z0-9:-]+$")
_ANNOUNCEMENT_LINK_RE = re.compile(r"^(/|https://)")


def validate_announcement_bar_setting(value: object) -> bool:
    """django-extra-settings validator for ``ANNOUNCEMENT_BAR``.

    Boolean contract, like ``validate_business_hours_setting``.

    Shape: ``{"enabled": bool, "text": str, "i18n": {"<locale>":
    {"text": str}}, "link"?, "icon"?, "color"?, "dismissible"?,
    "id"?}``. ``text`` carries the DEFAULT locale's wording and
    ``i18n`` overrides it per locale — the same partial-override
    convention as ``PageSection.i18n`` and ``STORE_OFFICES``, so the
    default locale is not a valid key there.

    ``id`` is what a dismissal is remembered against in the visitor's
    browser: changing it re-shows the bar to everyone, which is how a
    merchant runs a second announcement without every previous
    dismissal swallowing it.
    """
    if value in (None, "", {}):
        return True
    if not isinstance(value, dict):
        return False
    data = {str(k): v for k, v in value.items()}
    if set(data) - _ANNOUNCEMENT_KEYS:
        return False

    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        return False

    text = data.get("text", "")
    if not isinstance(text, str) or len(text) > 200:
        return False
    # A bar with nothing to say is a blank strip above the header.
    if enabled and not text.strip():
        return False

    for key, pattern, limit in (
        ("link", _ANNOUNCEMENT_LINK_RE, 1000),
        ("icon", _ICON_NAME_RE, 100),
    ):
        entry = data.get(key)
        if entry is None:
            continue
        if not isinstance(entry, str) or len(entry) > limit:
            return False
        if entry and not pattern.match(entry):
            return False

    color = data.get("color")
    if color is not None and color not in _ANNOUNCEMENT_COLORS:
        return False

    for key in ("dismissible",):
        entry = data.get(key)
        if entry is not None and not isinstance(entry, bool):
            return False

    identifier = data.get("id")
    if identifier is not None and (
        not isinstance(identifier, str) or len(identifier) > 64
    ):
        return False

    from core.utils.i18n import available_language_codes

    codes = available_language_codes()
    default = settings.PARLER_DEFAULT_LANGUAGE_CODE
    i18n = data.get("i18n", {})
    if not isinstance(i18n, dict):
        return False
    for code, override in i18n.items():
        if code not in codes or code == default:
            return False
        if not isinstance(override, dict) or set(override) - {"text"}:
            return False
        entry = override.get("text")
        if not isinstance(entry, str) or len(entry) > 200:
            return False

    return True


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
