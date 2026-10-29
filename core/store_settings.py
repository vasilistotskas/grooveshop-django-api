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
