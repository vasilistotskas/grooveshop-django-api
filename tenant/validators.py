"""Validators for tenant settings (``django-extra-settings``) and the
schema name. The tenant's structured JSON fields validate against JSON
Schemas instead (``tenant.json_schemas``)."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible
from django.utils.translation import gettext_lazy as _
from django_tenants.utils import get_public_schema_name

from core.store_setting_shapes import is_valid_structured

# ---------------------------------------------------------------------------
# Structured JSON settings
# ---------------------------------------------------------------------------
#
# ANNOUNCEMENT_BAR, AUTH_PANEL, FUNDING_BANNER, STORE_OFFICES and
# BUSINESS_HOURS are each a serializer in ``core/store_setting_shapes.py``:
# the same class validates the save here and describes the value in the
# OpenAPI schema. These are the dotted paths ``settings.py`` names; a
# validator returns a BOOLEAN (``Setting.validate()`` wraps a falsy result
# in its own ``ValidationError``).


def validate_announcement_bar_setting(value: object) -> bool:
    return is_valid_structured("ANNOUNCEMENT_BAR", value)


def validate_auth_panel_setting(value: object) -> bool:
    return is_valid_structured("AUTH_PANEL", value)


def validate_funding_banner_setting(value: object) -> bool:
    return is_valid_structured("FUNDING_BANNER", value)


def validate_store_offices_setting(value: object) -> bool:
    return is_valid_structured("STORE_OFFICES", value)


def validate_business_hours_setting(value: object) -> bool:
    return is_valid_structured("BUSINESS_HOURS", value)


# ---------------------------------------------------------------------------
# DISPATCH_CUTOFF extra_setting
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def validate_dispatch_cutoff_setting(value: object) -> bool:
    """django-extra-settings validator for ``DISPATCH_CUTOFF``.

    A zero-padded ``HH:MM`` local time, or empty for "no cutoff" (an
    order placed on a business day always dispatches that day).
    """
    if value in (None, ""):
        return True
    return isinstance(value, str) and bool(_TIME_RE.match(value))


# ---------------------------------------------------------------------------
# RETURN_WINDOW_DAYS / RETURN_FEES extra_settings
# ---------------------------------------------------------------------------

# Who pays to send a return back: the return is free to the customer, or
# the customer pays its shipping. Empty = the store has not said.
RETURN_FEES_CHOICES = frozenset({"", "free", "customer"})


def validate_return_window_days_setting(value: object) -> bool:
    """django-extra-settings validator for ``RETURN_WINDOW_DAYS``.

    A whole number of days, 0 for "not stated" (the storefront then
    states only the link to the return-policy page).
    """
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_return_fees_setting(value: object) -> bool:
    """django-extra-settings validator for ``RETURN_FEES``."""
    return value is None or value in RETURN_FEES_CHOICES


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
# STORE_GEO_LAT / STORE_GEO_LNG extra_settings
# ---------------------------------------------------------------------------

# Decimal degrees: an optional minus sign, digits, an optional fraction,
# and nothing else. Mirrored by the storefront's ``parseDegrees``
# (shared/utils/coordinates.ts), which reads the value the same way.
_DECIMAL_DEGREES_RE = re.compile(r"^-?\d{1,3}(\.\d+)?$")


def _degrees_ok(value: object, limit: int) -> bool:
    # Empty is the documented "no location" (no geo output, no map pin).
    if value in (None, ""):
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(_DECIMAL_DEGREES_RE.match(text)) and abs(float(text)) <= limit


def validate_latitude_setting(value: object) -> bool:
    """django-extra-settings validator for ``STORE_GEO_LAT``.

    The store's latitude is published as its location, on the map and in
    the LocalBusiness structured data, so a value the storefront cannot
    read as decimal degrees within ±90 is refused here rather than
    silently dropped there.
    """
    return _degrees_ok(value, 90)


def validate_longitude_setting(value: object) -> bool:
    """django-extra-settings validator for ``STORE_GEO_LNG`` (±180)."""
    return _degrees_ok(value, 180)


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


# ---------------------------------------------------------------------------
# Social profile URLs (``Tenant.socials_*``)
# ---------------------------------------------------------------------------

#: A country subdomain such as ``gr.pinterest.com`` or ``uk.linkedin.com``.
_COUNTRY_SUBDOMAIN_RE = re.compile(r"^[a-z]{2}$")


@deconstructible
class SocialProfileUrlValidator:
    """A link to a profile on one social network, not the network itself.

    The storefront renders each ``Tenant.socials_*`` value as a footer
    link and as the Organization's schema.org ``sameAs``, so it has to
    name the store's own page: ``https://twitter.com`` is a URL, but it
    identifies nobody. A value passes when it is ``https``, its host is
    one of ``domains`` (with or without ``www.``; a two-letter country
    subdomain too when ``country_subdomains`` is on, for the networks
    that serve profiles from ``gr.pinterest.com``), and its path names a
    profile (anything but ``/``). Blank is the model field's business
    (``blank=True``), never reaching a validator.

    The hosts are exact, so ``twitter.com.evil.example``, userinfo
    (``twitter.com@evil.example``) and ports do not pass.
    """

    code = "invalid_social_profile"

    def __init__(
        self, domains: Iterable[str], *, country_subdomains: bool = False
    ) -> None:
        self.domains = tuple(domains)
        self.country_subdomains = country_subdomains

    def __call__(self, value: str) -> None:
        if not self._is_profile(value):
            raise ValidationError(
                _(
                    "Enter the URL of your profile on %(hosts)s: https://, "
                    "one of those hosts and a path naming the profile, not "
                    "only the home page."
                ),
                code=self.code,
                params={"hosts": ", ".join(self.domains)},
            )

    def _is_profile(self, value: str) -> bool:
        try:
            parts = urlsplit(value)
            port = parts.port
        except ValueError:
            return False
        return (
            parts.scheme == "https"
            and port is None
            and parts.username is None
            and self._host_allowed(parts.hostname)
            and bool(parts.path.strip("/"))
        )

    def _host_allowed(self, host: str | None) -> bool:
        if not host:
            return False
        for domain in self.domains:
            if host in (domain, f"www.{domain}"):
                return True
            label, _dot, rest = host.partition(".")
            if (
                self.country_subdomains
                and rest == domain
                and _COUNTRY_SUBDOMAIN_RE.match(label)
            ):
                return True
        return False

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, SocialProfileUrlValidator)
            and other.domains == self.domains
            and other.country_subdomains == self.country_subdomains
        )

    def __hash__(self) -> int:
        return hash((self.domains, self.country_subdomains))


# ---------------------------------------------------------------------------
# TenantDomain.domain
# ---------------------------------------------------------------------------

HOSTNAME_MAX_LENGTH = 253

_HOSTNAME_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def validate_store_hostname(value: str) -> None:
    """Field validator for ``TenantDomain.domain``: a bare lowercase host.

    The storefront lower-cases the Host and strips its port, then both
    sides match a ``TenantDomain`` row EXACTLY, so any other spelling
    saves fine and never resolves. Uppercase is rejected, not lowered,
    so the stored text is what the operator typed. An IDN is stored as
    its ``xn--`` punycode form.
    """
    if not value:
        return
    if len(value) > HOSTNAME_MAX_LENGTH:
        raise ValidationError(
            _("A domain is at most %(max)d characters long."),
            code="domain_too_long",
            params={"max": HOSTNAME_MAX_LENGTH},
        )
    if "://" in value or any(char in value for char in "/?#@"):
        raise ValidationError(
            _("Enter the bare host name, without a scheme or a path."),
            code="domain_has_scheme_or_path",
        )
    try:
        ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        pass
    else:
        raise ValidationError(
            _("An IP address is not a store domain; use a host name."),
            code="domain_is_ip_address",
        )
    if ":" in value:
        raise ValidationError(
            _("Enter the host name without a port."),
            code="domain_has_port",
        )
    if value.endswith("."):
        raise ValidationError(
            _("Enter the host name without a trailing dot."),
            code="domain_trailing_dot",
        )
    if value != value.lower():
        raise ValidationError(
            _("Enter the host name in lowercase."),
            code="domain_not_lowercase",
        )
    labels = value.split(".")
    if len(labels) < 2:
        raise ValidationError(
            _("A domain needs at least two labels, such as shop.example.com."),
            code="domain_single_label",
        )
    if not all(_HOSTNAME_LABEL_RE.fullmatch(label) for label in labels):
        raise ValidationError(
            _(
                "Each part of a domain is 1 to 63 letters, digits or hyphens "
                "and neither starts nor ends with a hyphen."
            ),
            code="domain_invalid_label",
        )
