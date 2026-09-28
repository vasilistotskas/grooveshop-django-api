"""Convert the six legacy pricing ``Setting`` rows into ``ShippingRate``.

Before this migration, "the store's shipping price" lived in three
disconnected places: ``CHECKOUT_SHIPPING_PRICE``/``FREE_SHIPPING_
THRESHOLD`` for ``flat_rate`` (home delivery), ``BOXNOW_SHIPPING_
PRICE``/``BOXNOW_FREE_SHIPPING_THRESHOLD`` for BoxNow (pickup point),
and ``ACS_SHIPPING_PRICE``/``ACS_FREE_SHIPPING_THRESHOLD`` for ACS
(home delivery + Smartpoint pickup) — none of them country-aware. This
migration copies each existing tenant's values into ``ShippingRate``
rows so pricing keeps working unchanged once the code stops reading
the Setting rows (release N+1, ``0012_drop_legacy_shipping_settings``).

Countries come from ``ShippingProvider.metadata['supported_countries']``
when a provider has that key set, else ``["GR"]`` — so a store that
never touched the key keeps shipping to exactly the country it already
serves, and does not silently start accepting Cyprus orders on the day
this migration runs.

Reads each Setting row's frozen ``value_decimal`` column directly
(never a live ``Setting.get()`` call, which would follow the runtime
cache/fallback chain this migration is deliberately independent of)
and falls back to the value ``settings.EXTRA_SETTINGS_DEFAULTS`` seeded
when the row is missing — a frozen historical default, not a runtime
fallback: these numbers must stay exactly what the six keys defaulted
to on this date even if a later release changes those defaults.

Idempotent — ``get_or_create`` keyed on the model's own
(provider, country, kind) uniqueness. Reverse deletes every row this
migration could have created (all rates for the three provider codes,
every country) — the same coarse, non-precious rule
``0009_seed_flat_rate_provider`` uses for its own reverse.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import migrations

# (provider_code, kind, setting_price_name, setting_threshold_name,
#  frozen historical default price, frozen historical default threshold)
_CONVERSIONS: tuple[tuple[str, str, str, str, Decimal, Decimal], ...] = (
    (
        "flat_rate",
        "home_delivery",
        "CHECKOUT_SHIPPING_PRICE",
        "FREE_SHIPPING_THRESHOLD",
        Decimal("3.00"),
        Decimal("50.00"),
    ),
    (
        "boxnow",
        "pickup_point",
        "BOXNOW_SHIPPING_PRICE",
        "BOXNOW_FREE_SHIPPING_THRESHOLD",
        Decimal("2.50"),
        Decimal("30.00"),
    ),
    (
        "acs",
        "home_delivery",
        "ACS_SHIPPING_PRICE",
        "ACS_FREE_SHIPPING_THRESHOLD",
        Decimal("3.50"),
        Decimal("40.00"),
    ),
    (
        "acs",
        "pickup_point",
        "ACS_SHIPPING_PRICE",
        "ACS_FREE_SHIPPING_THRESHOLD",
        Decimal("3.50"),
        Decimal("40.00"),
    ),
)

_PROVIDER_CODES = ("flat_rate", "boxnow", "acs")

_KIND_SUPPORT_FLAG = {
    "home_delivery": "supports_home_delivery",
    "pickup_point": "supports_pickup_point",
}

_DEFAULT_COUNTRIES = ["GR"]


def _setting_decimal(Setting, db_alias, name, default) -> Decimal:
    row = Setting.objects.using(db_alias).filter(name=name).first()
    if row is None:
        return default
    return row.value_decimal


def convert_legacy_pricing(apps, schema_editor):
    ShippingProvider = apps.get_model("shipping", "ShippingProvider")
    ShippingRate = apps.get_model("shipping", "ShippingRate")
    Country = apps.get_model("country", "Country")
    Setting = apps.get_model("extra_settings", "Setting")
    db_alias = schema_editor.connection.alias

    providers = {
        provider.code: provider
        for provider in ShippingProvider.objects.using(db_alias).filter(
            code__in=_PROVIDER_CODES
        )
    }

    for (
        provider_code,
        kind,
        price_setting_name,
        threshold_setting_name,
        default_price,
        default_threshold,
    ) in _CONVERSIONS:
        provider = providers.get(provider_code)
        if provider is None:
            # Earlier seed migrations should have created every one of
            # these rows; nothing to convert if one is missing.
            continue
        if not getattr(provider, _KIND_SUPPORT_FLAG[kind]):
            continue

        price = _setting_decimal(
            Setting, db_alias, price_setting_name, default_price
        )
        threshold = _setting_decimal(
            Setting, db_alias, threshold_setting_name, default_threshold
        )

        country_codes = (
            provider.metadata or {}
        ).get("supported_countries") or _DEFAULT_COUNTRIES

        for country_code in country_codes:
            country = Country.objects.using(db_alias).filter(
                alpha_2=country_code.upper()
            ).first()
            if country is None:
                # A code in an operator-edited ``supported_countries``
                # list that doesn't exist in ``country_country`` has
                # nothing to attach a rate to.
                continue

            ShippingRate.objects.using(db_alias).get_or_create(
                provider=provider,
                country=country,
                kind=kind,
                defaults={
                    "price": price,
                    "price_currency": "EUR",
                    "free_shipping_threshold": threshold,
                    "free_shipping_threshold_currency": "EUR",
                    "max_weight_grams": None,
                    "is_active": True,
                },
            )


def unconvert_legacy_pricing(apps, schema_editor):
    ShippingRate = apps.get_model("shipping", "ShippingRate")
    ShippingRate.objects.using(schema_editor.connection.alias).filter(
        provider__code__in=_PROVIDER_CODES
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("shipping", "0010_shippingrate"),
    ]

    operations = [
        migrations.RunPython(
            convert_legacy_pricing,
            unconvert_legacy_pricing,
            elidable=False,
        ),
    ]
