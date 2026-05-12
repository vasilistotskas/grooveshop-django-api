"""Read ACS configuration from ``ShippingProvider.metadata``.

Single source of truth for "structural" per-provider config (locker
kinds, nearest-search limit, weight bounds, default country, map
chrome). Hardcoded defaults live alongside each accessor so the code
path always works even when the metadata seed migration hasn't run
yet (e.g. on a fresh `migrate --run-syncdb` in CI).

All accessors are cheap — a single ``.objects.only("metadata").first()``
call per request. They cache the metadata dict on the resolver to
avoid re-querying inside a single hot path; callers that mutate the
provider row in tests should call :func:`reset_cache` afterwards.

Why JSONField metadata and not ``extra_settings.Setting``:
* Settings are global; metadata is keyed by provider, so two carriers
  can disagree on the same logical key.
* Metadata is already loaded with the registry on every dispatch —
  zero new queries when an adapter looks itself up.
* Operators tune metadata from Django admin, no redeploy needed.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.conf import settings as django_settings

logger = logging.getLogger(__name__)

# Fallback constants — used only when the metadata seed migration
# hasn't run yet OR when an operator deletes a key. Document any
# change here in ``shipping/migrations/0004_seed_provider_metadata.py``
# so the seed and the fallback agree.
_DEFAULT_SHOP_KINDS_BY_COUNTRY: dict[str, list[int]] = {
    "GR": [7, 8],
    "CY": [7],
}
_DEFAULT_NEAREST_LIMIT = 20
_DEFAULT_MIN_WEIGHT_KG = Decimal("0.5")
_DEFAULT_MAX_WEIGHT_KG = Decimal("999")
_DEFAULT_VOUCHER_LANGUAGE = "GR"
# ACS_Print_Voucher Print_Type values (per the ACS REST API PDF):
# 1 = thermal/roll printer (single voucher per page); 2 = laser
# (4 vouchers per A4). Ops uses thermal printers, so 1 is the
# default — admins can flip via ``ShippingProvider.metadata["print_type"]``.
_DEFAULT_PRINT_TYPE = 1


def _provider_metadata() -> dict[str, Any]:
    """Return the ``acs`` provider's metadata dict, or ``{}`` on miss.

    Lazy import inside the function keeps this module importable from
    everywhere (including ``shipping_acs/services.py`` at import time)
    without circular-import risk on ``ShippingProvider``.
    """
    try:
        from shipping.models import ShippingProvider
    except Exception:
        return {}

    try:
        provider = (
            ShippingProvider.objects.filter(code="acs").only("metadata").first()
        )
    except Exception:
        # DB not ready (e.g. during ``manage.py check`` before
        # migrations) — fall back to constants.
        return {}

    return dict(provider.metadata or {}) if provider else {}


def shop_kinds_for_country(country_code: str) -> tuple[int, ...]:
    """Return the locker shop_kind values for ``country_code``.

    Falls back to the generic Greek catalogue (7+8) when the country
    isn't in metadata — keeps the picker functional for any new
    country until ops add an entry.
    """
    metadata = _provider_metadata()
    by_country: dict[str, list[int]] = (
        metadata.get("shop_kinds_by_country") or _DEFAULT_SHOP_KINDS_BY_COUNTRY
    )
    kinds = by_country.get(country_code.upper())
    if not kinds:
        # Defensive default — try the first-listed country in metadata
        # before falling back to the constants.
        first = next(iter(by_country.values()), None)
        kinds = first or _DEFAULT_SHOP_KINDS_BY_COUNTRY["GR"]
    return tuple(int(k) for k in kinds)


def all_locker_kinds() -> tuple[int, ...]:
    """Union of every locker kind across all configured countries.

    Used by the ``list``/``nearest`` viewset endpoints when no country
    is specified — broader than ``shop_kinds_for_country`` but still
    excludes generic Shop / Kiosk rows.
    """
    metadata = _provider_metadata()
    by_country: dict[str, list[int]] = (
        metadata.get("shop_kinds_by_country") or _DEFAULT_SHOP_KINDS_BY_COUNTRY
    )
    seen: set[int] = set()
    for kinds in by_country.values():
        for k in kinds:
            seen.add(int(k))
    return tuple(sorted(seen)) or tuple(_DEFAULT_SHOP_KINDS_BY_COUNTRY["GR"])


def nearest_limit() -> int:
    """Cap for the ``ACS_StationViewSet.nearest`` endpoint."""
    metadata = _provider_metadata()
    raw = metadata.get("nearest_limit", _DEFAULT_NEAREST_LIMIT)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return _DEFAULT_NEAREST_LIMIT


def min_voucher_weight_kg() -> Decimal:
    """ACS minimum chargeable weight (per their PDF) — usually 0.5 kg."""
    metadata = _provider_metadata()
    raw = metadata.get("min_weight_kg", _DEFAULT_MIN_WEIGHT_KG)
    try:
        return Decimal(str(raw))
    except Exception:
        return _DEFAULT_MIN_WEIGHT_KG


def max_voucher_weight_kg() -> Decimal:
    """Defensive upper bound — clamps a bad source weight."""
    metadata = _provider_metadata()
    raw = metadata.get("max_weight_kg", _DEFAULT_MAX_WEIGHT_KG)
    try:
        return Decimal(str(raw))
    except Exception:
        return _DEFAULT_MAX_WEIGHT_KG


def default_country() -> str:
    """Country to use when an order/cart has no explicit country.

    Reads ``settings.ACS_SUPPORTED_COUNTRIES[0]`` first (existing env
    var), then falls back to the first key in metadata, then 'GR'.
    """
    supported = getattr(django_settings, "ACS_SUPPORTED_COUNTRIES", None)
    if supported:
        first = supported[0] if isinstance(supported, list | tuple) else None
        if first:
            return str(first).upper()
    metadata = _provider_metadata()
    by_country = metadata.get("shop_kinds_by_country") or {}
    if by_country:
        return next(iter(by_country.keys())).upper()
    return "GR"


def default_voucher_language() -> str:
    """Voucher Language field — defaults to GR (Greek)."""
    metadata = _provider_metadata()
    return str(
        metadata.get("default_voucher_language") or _DEFAULT_VOUCHER_LANGUAGE
    )


def print_type() -> int:
    """ACS_Print_Voucher ``Print_Type`` value — defaults to thermal (1).

    ACS only accepts two values: ``1`` for thermal/roll printers
    (single voucher per page) and ``2`` for laser (4 vouchers per
    A4). Any other value is clamped to the default so a bad admin
    entry can't dead-letter the label download flow.
    """
    metadata = _provider_metadata()
    raw = metadata.get("print_type", _DEFAULT_PRINT_TYPE)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_PRINT_TYPE
    if value not in (1, 2):
        return _DEFAULT_PRINT_TYPE
    return value


def station_origin() -> str | None:
    """Merchant pickup-station code for ``ACS_Price_Calculation``.

    The price-calc endpoint requires both ``Acs_Station_Origin`` (the
    merchant's pickup branch) and ``Acs_Station_Destination`` (the
    recipient's branch, resolved via address validation). Empty values
    return ``Άγνωστο κατάστημα παραλαβής``.

    Resolution order:

    1. ``ShippingProvider.metadata['station_origin']`` — operator
       override via Django admin.
    2. Parsed from ``settings.ACS_BILLING_CODE`` positions 1-2 — the
       standard ACS billing code format is ``<category><station>
       <customer_id>``, e.g. ``"2ΑΚ89587"`` → ``"ΑΚ"``.
    3. ``None`` — the live-quote path treats this as "can't quote" and
       falls back to the flat-rate Setting.

    Returns the station code as ACS expects it (Greek 2-letter codes
    like ``ΑΚ`` / ``ΓΣ`` / ``ΘΕ``).
    """
    metadata = _provider_metadata()
    explicit = metadata.get("station_origin")
    if explicit:
        return str(explicit).strip().upper() or None

    billing_code = getattr(django_settings, "ACS_BILLING_CODE", "") or ""
    if len(billing_code) >= 3:
        candidate = billing_code[1:3].strip().upper()
        if candidate:
            return candidate
    return None


def map_config() -> dict[str, Any]:
    """Map chrome (centre/zoom/tile providers) for the picker UI.

    Surfaced verbatim by the ``/api/v1/shipping/options`` response and
    consumed by the Nuxt SmartpointMap component. No defaults baked
    in here — when metadata is empty the frontend uses its own
    fallbacks (Athens centre, CARTO tiles).
    """
    metadata = _provider_metadata()
    return {
        "default_map_center": metadata.get("default_map_center"),
        "default_map_zoom": metadata.get("default_map_zoom"),
        "tile_provider": metadata.get("tile_provider"),
    }
