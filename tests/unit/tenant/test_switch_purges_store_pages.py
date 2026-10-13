"""A store feature switch purges every page the storefront cached for it.

The storefront's navigation, footer and product cards show what a store
offers, and it serves cached renders of its pages (up to an hour for the
static ones). Nothing purged them on a switch, so a feature switched off
stayed on show until each copy expired.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import call, patch

import pytest
from django.conf import settings
from extra_settings.models import Setting

from core.cache.surfaces import STORE_PAGES_SURFACE
from core.tasks import purge_store_pages_task
from tenant.features import (
    feature_plan_flags,
    feature_setting_gates,
    feature_setting_keys,
)
from tests.utils.staff import store_tenant
from tests.utils.store_settings import switch_store_settings

pytestmark = pytest.mark.django_db

DISPATCH = "tenant.switches.dispatch_on_commit"


def purges(dispatch) -> list:
    return [
        c
        for c in dispatch.call_args_list
        if c.args[0] is purge_store_pages_task
    ]


@pytest.mark.parametrize(
    "gate", feature_setting_gates(), ids=lambda gate: gate.setting_key
)
def test_every_feature_setting_purges_its_store_at_once(
    bind_tenant_and_schema, gate
):
    bind_tenant_and_schema(SimpleNamespace(schema_name="shop_one"))
    current = bool(Setting.get(gate.setting_key, default=gate.default))

    with patch(DISPATCH) as dispatch:
        switch_store_settings(**{gate.setting_key: not current})

    assert purges(dispatch) == [
        call(purge_store_pages_task, schema_name="shop_one")
    ]


def test_another_store_setting_purges_nothing(bind_tenant_and_schema):
    assert "AGENT_COMMERCE_ENABLED" not in feature_setting_keys()
    bind_tenant_and_schema(SimpleNamespace(schema_name="shop_one"))
    current = bool(Setting.get("AGENT_COMMERCE_ENABLED", default=False))

    with patch(DISPATCH) as dispatch:
        switch_store_settings(AGENT_COMMERCE_ENABLED=not current)

    assert purges(dispatch) == []


def test_saving_a_feature_setting_unchanged_purges_nothing(
    bind_tenant_and_schema,
):
    bind_tenant_and_schema(SimpleNamespace(schema_name="shop_one"))
    switch_store_settings(PRODUCT_REVIEWS_ENABLED=False)

    with patch(DISPATCH) as dispatch:
        switch_store_settings(PRODUCT_REVIEWS_ENABLED=False)

    assert purges(dispatch) == []


def test_the_platforms_own_setting_purges_nothing():
    with patch(DISPATCH) as dispatch:
        switch_store_settings(PRODUCT_REVIEWS_ENABLED=False)
        switch_store_settings(PRODUCT_REVIEWS_ENABLED=True)

    assert purges(dispatch) == []


@pytest.mark.parametrize("flag", feature_plan_flags())
def test_every_plan_flag_purges_once_the_storefronts_copy_expired(flag):
    """The storefront reads plan flags from its copy of the resolve
    payload, so a purge before that copy expires could cache a page
    rendered from the old flag all over again."""
    tenant = store_tenant(f"shop_{flag}", **{flag: True})

    with patch(DISPATCH) as dispatch:
        setattr(tenant, flag, False)
        tenant.save()

    assert purges(dispatch) == [
        call(
            purge_store_pages_task,
            schema_name=f"shop_{flag}",
            countdown=settings.STOREFRONT_TENANT_CACHE_SECONDS,
        )
    ]


def test_a_tenant_save_that_leaves_the_flags_alone_purges_nothing():
    tenant = store_tenant("shop_rename")

    with patch(DISPATCH) as dispatch:
        tenant.store_name = "Renamed"
        tenant.save()

    assert purges(dispatch) == []


def test_a_partial_tenant_save_without_a_flag_purges_nothing():
    tenant = store_tenant("shop_partial", blog_enabled=True)

    with patch(DISPATCH) as dispatch:
        tenant.blog_enabled = False
        tenant.save(update_fields=["store_name"])

    assert purges(dispatch) == []


def test_the_task_purges_the_store_pages_surface_alone():
    with patch("core.cache.service.CacheService.purge") as purge:
        purge_store_pages_task.run()

    purge.assert_called_once_with([STORE_PAGES_SURFACE], include_related=False)
