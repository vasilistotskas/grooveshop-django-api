"""Read a store setting with the default it is declared with.

Store settings are django-extra-settings rows, one per name per schema,
seeded from ``settings.EXTRA_SETTINGS_DEFAULTS`` on every ``migrate``.
A row can still be missing (an admin deleted it), and ``Setting.get``
then returns whatever default its caller passes. Callers used to pass
their own literal, so the default lived in two places and drifted:
``STOCK_RESERVATION_TTL_MINUTES`` fell back to 15 against a declared 30,
``CONTACT_ATTACHMENTS_TYPES`` to no types against PDF, and the payment
hold settings to ``None``, which crashed. Read every store setting here,
so a missing row behaves exactly like a freshly seeded one.
"""

from collections.abc import Iterable
from typing import Any

from django.conf import settings
from extra_settings import cache as setting_cache
from extra_settings.cache import _get_cache, _get_cache_key
from extra_settings.models import Setting

DECLARED_DEFAULTS: dict[str, Any] = {
    entry["name"]: entry["value"] for entry in settings.EXTRA_SETTINGS_DEFAULTS
}


# The type each setting is declared with ('string', 'int', 'decimal',
# 'bool', 'json', ...).
DECLARED_TYPES: dict[str, str] = {
    entry["name"]: entry["type"] for entry in settings.EXTRA_SETTINGS_DEFAULTS
}


def declared_default(name: str) -> Any:
    """The value ``name`` is declared with in ``EXTRA_SETTINGS_DEFAULTS``.

    Raises ``KeyError`` for a name that is not declared: every store
    setting is, so an undeclared one is a typo or a missing declaration,
    never something to read with a made-up fallback.
    """
    try:
        return DECLARED_DEFAULTS[name]
    except KeyError:
        raise KeyError(
            f"{name} is not a store setting: declare it in "
            "settings.EXTRA_SETTINGS_DEFAULTS"
        ) from None


def store_setting(name: str) -> Any:
    """The active schema's value of ``name``, else its declared default."""
    return Setting.get(name, default=declared_default(name))


def store_settings(names: Iterable[str]) -> dict[str, Any]:
    """``store_setting`` for several names, in one query at most.

    ``Setting.get`` caches per key and only warms the others after a
    hit on a row, so a cold cache, or a store missing some rows, pays a
    query per key. This reads the library's cache for every name in ONE
    ``get_many`` (a Redis round trip is ~1.6 ms in production, so 28
    single reads were ~45 ms), then fetches the misses together and
    caches them the way the library does. A row holding ``None`` counts
    as no row, as in ``Setting.get``.
    """
    names = tuple(names)
    defaults = {name: declared_default(name) for name in names}
    keys = {name: _get_cache_key(name) for name in names}
    cached = _get_cache().get_many(keys.values())
    values = {name: cached.get(key) for name, key in keys.items()}
    missing = [name for name, value in values.items() if value is None]
    if missing:
        for row in Setting.objects.filter(name__in=missing):
            values[row.name] = row.value
            setting_cache.set_cached_setting(row.name, row.value)
    return {
        name: defaults[name] if value is None else value
        for name, value in values.items()
    }


# The store VALUES a storefront renders: figures, texts, hours, contact
# details. A store switch is never here: a feature is served in
# ``TenantConfig.features`` and a preference in ``TenantConfig.preferences``
# (``tenant/features.py``), Django's one answer for each, and a switch
# published here too would be a second answer a client could read instead.
# ``tests/integration/core/test_settings_public_api.py`` refuses one.
PUBLIC_SETTING_KEYS = frozenset(
    {
        # Shipping price/free-threshold is a per-country ``ShippingRate``
        # row now (``/api/v1/shipping/options`` and
        # ``/api/v1/shipping/free-shipping-info``), not a Setting. The
        # carriers' own settings (e.g. ACS_DYNAMIC_PRICING_ENABLED) are
        # server-side only and stay admin-gated.
        # Storefront's "order before HH:MM" line next to a rate's
        # delivery days.
        "DISPATCH_CUTOFF",
        # The return terms the storefront's Organization schema.org node
        # states (with the countries the store ships to), the same terms
        # the store's public return-policy page gives.
        "RETURN_WINDOW_DAYS",
        "RETURN_FEES",
        # Purchase-form bounds for the storefront gift-card page.
        "GIFT_CARD_MIN_AMOUNT",
        "GIFT_CARD_MAX_AMOUNT",
        # Drives the storefront's "Valid for N years" line; the server
        # reads it again when it stamps ``expires_at``.
        "GIFT_CARD_VALIDITY_DAYS",
        # The loyalty programme's figures the storefront explains and
        # estimates with. Whether the programme is on is the ``loyalty``
        # feature.
        "LOYALTY_REDEMPTION_RATIO_EUR",
        "LOYALTY_POINTS_FACTOR",
        "LOYALTY_TIER_MULTIPLIER_ENABLED",
        "LOYALTY_POINTS_EXPIRATION_DAYS",
        "LOYALTY_NEW_CUSTOMER_BONUS_ENABLED",
        "LOYALTY_NEW_CUSTOMER_BONUS_POINTS",
        "LOYALTY_XP_PER_LEVEL",
        # Store presence: hours/geo feed the business-hours section,
        # footer open/closed badge and LocalBusiness schema.org.
        "BUSINESS_HOURS",
        "STORE_GEO_LAT",
        "STORE_GEO_LNG",
        # Public offices — the footer's contact column and the contact
        # page. Public by definition: these are the addresses the
        # merchant publishes for customers to visit, which is why they
        # are here and the INVOICE_SELLER_* seat is not.
        "STORE_OFFICES",
        # The three the storefront needs to render the contact form's
        # upload control and pre-check a file before spending a request
        # on it (whether uploads are on is the ``contact_attachments``
        # feature). The server re-reads them - a client is not a gate -
        # and the retention window is deliberately NOT here: it is
        # operator/legal policy, not something a visitor's form needs.
        "CONTACT_ATTACHMENTS_MAX_COUNT",
        "CONTACT_ATTACHMENTS_MAX_MB",
        "CONTACT_ATTACHMENTS_TYPES",
        # Public contact email for storefront footer/contact page.
        # Falls back to Tenant.contact_email or settings.INFO_EMAIL.
        "CONTACT_EMAIL",
        # The strip above the header. Operator-authored text rendered on
        # every page, so it is public by construction.
        "ANNOUNCEMENT_BAR",
        # The sign-in pages' photo and line. Operator-authored and shown
        # to every visitor before they authenticate, so public.
        "AUTH_PANEL",
        # The EU/ESPA co-funding strip at the very top of every page.
        # Operator-authored and shown to every visitor, so public.
        "FUNDING_BANNER",
        # Demo-store credentials, deliberately public: the login page
        # shows them so a prospect can see the logged-in storefront.
        # Empty everywhere by default; the demo seeder writes them only
        # on a tenant flagged ``is_demo``, and a nightly task resets
        # that account's data. Whether the card shows is the
        # ``demo_account`` preference in TenantConfig.preferences. Read
        # ``settings.EXTRA_SETTINGS_DEFAULTS`` before enabling this on
        # anything that is not a throwaway store.
        "DEMO_ACCOUNT_EMAIL",
        "DEMO_ACCOUNT_PASSWORD",
        "DEMO_ACCOUNT_B2B_EMAIL",
        "DEMO_ACCOUNT_B2B_PASSWORD",
    }
)
