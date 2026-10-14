"""A real tenant schema, entered with ``schema_context``, reads its row.

``schema_context(name)`` binds a bare django-tenants ``FakeTenant`` with
only ``schema_name``. The order commit hooks (``order/signals/handlers.py``)
re-enter their schema that way, so before ``bound_tenant`` every
``tenant.credentials`` helper read an empty value there: on production
(2026-10-13) shipping an order on a store with myDATA configured never
dispatched the receipt. This runs against the real tenant router, with
the tenant's own schema active, which ``tests/`` (public schema only)
cannot reproduce.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.db import connection
from django_tenants.postgresql_backend.base import FakeTenant
from django_tenants.utils import schema_context, tenant_context

from order.mydata.config import load_config
from tenant import credentials
from tenant.models import Tenant

pytestmark = pytest.mark.django_db

_SETTINGS = {
    "MYDATA_ENABLED": True,
    "MYDATA_FHM_EXEMPTION_GROUND": "ΠΟΛ.1002/2014 (test fixture)",
    "MYDATA_AUTO_SUBMIT": True,
}


def _setting_get(name, default=None):
    return _SETTINGS.get(name, default)


@pytest.fixture
def configured(mt_tenant):
    Tenant.objects.filter(pk=mt_tenant.pk).update(
        mydata_user_id="aade-user",
        mydata_subscription_key="sub-key",
        acs_api_key="acs-key",
        box_now_client_id="boxnow-client",
        meta_pixel_id="pixel-1",
    )
    return Tenant.objects.get(pk=mt_tenant.pk)


def test_schema_context_binds_a_fake_tenant_and_the_reader_upgrades_it(
    configured,
):
    with schema_context(configured.schema_name):
        assert isinstance(connection.tenant, FakeTenant)
        assert credentials.mydata_credentials()["user_id"] == "aade-user"
        assert credentials.acs_credentials()["api_key"] == "acs-key"
        assert credentials.box_now_credentials()["client_id"] == "boxnow-client"
        assert credentials.tenant_meta_pixel_id() == "pixel-1"


def test_myDATA_config_is_ready_under_schema_context(configured):
    with (
        patch("order.mydata.config.Setting.get", side_effect=_setting_get),
        schema_context(configured.schema_name),
    ):
        assert load_config().issues_automatically() is True


def test_schema_context_and_tenant_context_agree(configured):
    with schema_context(configured.schema_name):
        under_schema = credentials.mydata_credentials()
    with tenant_context(configured):
        under_tenant = credentials.mydata_credentials()

    assert under_schema == under_tenant


def test_another_tenant_never_inherits_the_credentials(configured, mt_tenant_b):
    with schema_context(mt_tenant_b.schema_name):
        assert credentials.mydata_credentials() == {
            "user_id": "",
            "subscription_key": "",
        }
        assert credentials.acs_credentials()["api_key"] == ""
