"""A store setting reads as its row, else as the value it is declared with.

Callers used to pass their own fallback to ``Setting.get``, and those
drifted from ``settings.EXTRA_SETTINGS_DEFAULTS``: a store missing its
``STOCK_RESERVATION_TTL_MINUTES`` row held stock for 15 minutes against
a declared 30, and one missing a payment-hold row crashed on ``None``.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from django.conf import settings
from extra_settings.models import Setting

from core.store_settings import (
    DECLARED_DEFAULTS,
    declared_default,
    store_setting,
    store_settings,
)

pytestmark = pytest.mark.django_db


def test_every_declared_setting_is_known():
    assert set(DECLARED_DEFAULTS) == {
        entry["name"] for entry in settings.EXTRA_SETTINGS_DEFAULTS
    }


def test_an_undeclared_name_is_refused():
    with pytest.raises(KeyError, match="EXTRA_SETTINGS_DEFAULTS"):
        store_setting("NO_SUCH_STORE_SETTING")


def test_a_row_wins_over_the_declared_value():
    Setting.objects.filter(name="STOCK_RESERVATION_TTL_MINUTES").update(
        value_int=45
    )

    assert store_setting("STOCK_RESERVATION_TTL_MINUTES") == 45


def test_a_missing_row_reads_as_the_declared_value():
    Setting.objects.filter(name="STOCK_RESERVATION_TTL_MINUTES").delete()

    assert store_setting("STOCK_RESERVATION_TTL_MINUTES") == declared_default(
        "STOCK_RESERVATION_TTL_MINUTES"
    )


def test_a_django_setting_of_the_same_name_is_never_read(settings):
    """django-extra-settings' conf fallback is off: a missing row never
    reads a same-named attribute of the Django settings module."""
    settings.STOCK_RESERVATION_TTL_MINUTES = 999
    Setting.objects.filter(name="STOCK_RESERVATION_TTL_MINUTES").delete()

    assert store_setting("STOCK_RESERVATION_TTL_MINUTES") == declared_default(
        "STOCK_RESERVATION_TTL_MINUTES"
    )


def test_several_settings_resolve_like_one(django_assert_max_num_queries):
    Setting.objects.filter(name="GIFT_CARD_MIN_AMOUNT").update(
        value_decimal=Decimal("25.00")
    )
    Setting.objects.filter(name="GIFT_CARD_MAX_AMOUNT").delete()
    names = ("GIFT_CARD_MIN_AMOUNT", "GIFT_CARD_MAX_AMOUNT")

    with django_assert_max_num_queries(1):
        values = store_settings(names)

    assert values == {name: store_setting(name) for name in names}
    assert values["GIFT_CARD_MIN_AMOUNT"] == Decimal("25.00")
    assert values["GIFT_CARD_MAX_AMOUNT"] == declared_default(
        "GIFT_CARD_MAX_AMOUNT"
    )


def test_several_settings_are_read_from_the_cache_in_one_round_trip():
    """A Redis round trip costs ~1.6 ms in production: 28 single reads
    were ~45 ms of ``settings/public``."""
    from core.caches import CustomCache

    names = ("GIFT_CARD_MIN_AMOUNT", "GIFT_CARD_MAX_AMOUNT", "RETURN_FEES")
    store_settings(names)  # warm every key

    with (
        patch.object(CustomCache, "get", autospec=True) as single,
        patch.object(
            CustomCache, "get_many", autospec=True, wraps=None
        ) as many,
    ):
        many.side_effect = lambda self, keys, version=None: {}
        store_settings(names)

    assert single.call_count == 0
    assert many.call_count == 1
