"""``TenantConfig.preferences``: each store preference's state.

The switches the API does not refuse on (``STORE_PREFERENCES`` in
``tenant/features.py``) ride in the tenant payload beside ``features``, so a
client never reads them from a settings call that can fail, and never keeps
a default of its own for one.
"""

from __future__ import annotations

import pytest
from extra_settings.models import Setting

from core.store_settings import declared_default
from tenant.features import STORE_PREFERENCES, effective_preferences
from tenant.models import Tenant
from tenant.serializers import (
    StorePreferencesSerializer,
    TenantConfigSerializer,
)
from tests.utils.staff import store_tenant
from tests.utils.store_settings import switch_store_settings

pytestmark = pytest.mark.django_db


def _store() -> Tenant:
    return Tenant(schema_name="acme", name="Acme")


def test_the_api_names_every_preference_of_the_table():
    assert set(StorePreferencesSerializer().fields) == set(STORE_PREFERENCES)


@pytest.mark.parametrize("on", [True, False])
@pytest.mark.parametrize(("name", "key"), list(STORE_PREFERENCES.items()))
def test_each_preference_reads_its_store_setting(name, key, on):
    switch_store_settings(**{key: on})

    assert effective_preferences(_store())[name] is on


@pytest.mark.parametrize(("name", "key"), list(STORE_PREFERENCES.items()))
def test_a_store_without_the_row_reads_the_declared_value(name, key):
    Setting.objects.filter(name=key).delete()

    assert effective_preferences(_store())[name] is bool(declared_default(key))


def test_the_platform_is_not_a_store():
    switch_store_settings(**dict.fromkeys(STORE_PREFERENCES.values(), True))

    public = Tenant(schema_name="public", name="Platform")

    assert effective_preferences(public) == dict.fromkeys(
        STORE_PREFERENCES, False
    )


def test_resolve_serves_them_under_preferences():
    switch_store_settings(CART_ENABLED=False, MOBILE_BOTTOM_NAV_ENABLED=True)

    data = TenantConfigSerializer(store_tenant("preference_store")).data

    assert data["preferences"]["cart"] is False
    assert data["preferences"]["mobile_bottom_nav"] is True
    assert set(data["preferences"]) == set(STORE_PREFERENCES)
