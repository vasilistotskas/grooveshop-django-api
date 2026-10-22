"""``STOCK_RESERVATION_TTL_MINUTES`` is validated where it is saved.

``StockManager`` used to replace an unusable value with a constant of
its own (15 minutes, against the 30 the setting is declared with). The
admin now refuses one instead, and the manager reads the setting as is.
"""

from __future__ import annotations

import pytest
from extra_settings.models import Setting

from order.stock import StockManager, validate_reservation_ttl_minutes_setting


@pytest.mark.parametrize("value", [0, -5, True, "30", None])
def test_the_admin_refuses_a_ttl_that_is_not_a_positive_integer(value):
    assert validate_reservation_ttl_minutes_setting(value) is False


def test_the_admin_accepts_a_positive_ttl():
    assert validate_reservation_ttl_minutes_setting(45) is True


@pytest.mark.django_db
def test_the_manager_reads_the_stores_ttl():
    Setting.objects.filter(name="STOCK_RESERVATION_TTL_MINUTES").update(
        value_int=45
    )

    assert StockManager.get_reservation_ttl_minutes() == 45


@pytest.mark.django_db
def test_a_ttl_written_past_the_admin_refuses_to_reserve():
    """No reservation that expires on creation: it would sell the
    same stock twice."""
    Setting.objects.filter(name="STOCK_RESERVATION_TTL_MINUTES").update(
        value_int=0
    )

    with pytest.raises(ValueError, match="STOCK_RESERVATION_TTL_MINUTES"):
        StockManager.get_reservation_ttl_minutes()
