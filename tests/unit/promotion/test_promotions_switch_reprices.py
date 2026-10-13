"""Switching promotions off or on re-prices the whole store at once.

The 03:00 price sweep was the only net under the switch, so for up to a
day product lists and search showed promotion prices the cart no longer
honoured, or the other way round. The switch now queues that sweep for
the store it moved on, once the change commits.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from product.tasks import reevaluate_price_episodes
from tests.utils.staff import store_tenant
from tests.utils.store_settings import switch_store_settings

pytestmark = pytest.mark.django_db

DISPATCH = "promotion.signals.dispatch_on_commit"


def test_the_store_switch_reprices_its_store(bind_tenant_and_schema):
    bind_tenant_and_schema(SimpleNamespace(schema_name="shop_one"))

    with patch(DISPATCH) as dispatch:
        switch_store_settings(PROMOTIONS_ENABLED=True)

    dispatch.assert_called_once_with(
        reevaluate_price_episodes, schema_name="shop_one"
    )


def test_another_store_setting_reprices_nothing(bind_tenant_and_schema):
    bind_tenant_and_schema(SimpleNamespace(schema_name="shop_one"))

    with patch(DISPATCH) as dispatch:
        switch_store_settings(LOYALTY_ENABLED=True)

    dispatch.assert_not_called()


def test_the_platforms_own_setting_reprices_nothing():
    with patch(DISPATCH) as dispatch:
        switch_store_settings(PROMOTIONS_ENABLED=True)

    dispatch.assert_not_called()


def test_the_plan_flag_reprices_the_store_it_moved_on():
    tenant = store_tenant("shop_two", promotions_enabled=False)

    with patch(DISPATCH) as dispatch:
        tenant.promotions_enabled = True
        tenant.save()

    dispatch.assert_called_once_with(
        reevaluate_price_episodes, schema_name="shop_two"
    )


def test_a_tenant_save_that_leaves_the_flag_alone_reprices_nothing():
    tenant = store_tenant("shop_three", promotions_enabled=True)

    with patch(DISPATCH) as dispatch:
        tenant.name = "Renamed"
        tenant.save()

    dispatch.assert_not_called()


def test_saving_the_switch_unchanged_reprices_nothing(bind_tenant_and_schema):
    bind_tenant_and_schema(SimpleNamespace(schema_name="shop_one"))
    switch_store_settings(PROMOTIONS_ENABLED=True)

    with patch(DISPATCH) as dispatch:
        switch_store_settings(PROMOTIONS_ENABLED=True)

    dispatch.assert_not_called()


def test_a_partial_tenant_save_without_the_flag_reprices_nothing():
    tenant = store_tenant("shop_four", promotions_enabled=False)

    with patch(DISPATCH) as dispatch:
        tenant.promotions_enabled = True
        tenant.save(update_fields=["name"])

    dispatch.assert_not_called()
