"""Presence is per store: a heartbeat on one store is invisible on another.

The cache key function puts the active schema in front of every key, so the
sets and records ``builder.presence`` keeps cannot cross stores.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache
from django_tenants.utils import schema_context

from builder import presence

pytestmark = pytest.mark.django_db

HOME = "template:home"


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


def test_a_heartbeat_on_store_a_is_invisible_on_store_b(mt_tenant, mt_tenant_b):
    with schema_context(mt_tenant.schema_name):
        presence.heartbeat(staff=1, name="Maria", documents=[HOME])

    with schema_context(mt_tenant_b.schema_name):
        seen = presence.heartbeat(staff=2, name="Nikos", documents=[HOME])

    assert seen == {HOME: []}
    with schema_context(mt_tenant.schema_name):
        seen = presence.heartbeat(staff=2, name="Nikos", documents=[HOME])
    assert [entry["name"] for entry in seen[HOME]] == ["Maria"]


def test_leaving_one_store_leaves_the_other(mt_tenant, mt_tenant_b):
    for tenant in (mt_tenant, mt_tenant_b):
        with schema_context(tenant.schema_name):
            presence.heartbeat(staff=1, name="Maria", documents=[HOME])

    with schema_context(mt_tenant.schema_name):
        presence.leave(1)

    with schema_context(mt_tenant_b.schema_name):
        seen = presence.heartbeat(staff=2, name="Nikos", documents=[HOME])
    assert [entry["name"] for entry in seen[HOME]] == ["Maria"]
