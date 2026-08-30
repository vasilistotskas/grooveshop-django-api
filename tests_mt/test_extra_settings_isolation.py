"""``extra_settings`` is dual-listed (SHARED_APPS AND TENANT_APPS) so
each schema keeps its own override table — a merchant's ``Setting``
value must never leak to, or be overwritten by, another schema's copy
of the same setting name. The main suite patches ``extra_settings``
onto a ``DummyCache`` specifically to sidestep schema-aware caching
(see ``tests/conftest.py``), so this is untested there.
"""

from __future__ import annotations

import pytest
from django_tenants.utils import schema_context

_SETTING_NAME = "MT_ISOLATION_PROBE"


@pytest.fixture(autouse=True)
def _cleanup_probe_setting():
    yield
    from extra_settings.models import Setting

    with schema_context("public"):
        Setting.objects.filter(name=_SETTING_NAME).delete()


@pytest.mark.django_db
def test_setting_value_is_schema_scoped(mt_tenant):
    from extra_settings.models import Setting

    with schema_context(mt_tenant.schema_name):
        Setting.objects.filter(name=_SETTING_NAME).delete()
        Setting.objects.update_or_create(
            name=_SETTING_NAME,
            defaults={"value_type": "string", "value_string": "tenant-value"},
        )
        assert Setting.get(_SETTING_NAME, default="") == "tenant-value"

    with schema_context("public"):
        assert Setting.get(_SETTING_NAME, default="") == "", (
            "a Setting created in the tenant schema was readable from "
            "public — extra_settings rows are not schema-isolated"
        )
        Setting.objects.update_or_create(
            name=_SETTING_NAME,
            defaults={"value_type": "string", "value_string": "public-value"},
        )

    with schema_context(mt_tenant.schema_name):
        assert Setting.get(_SETTING_NAME, default="") == "tenant-value", (
            "public's write to the same setting name overwrote the "
            "tenant's own value — extra_settings rows are colliding "
            "cross-schema"
        )
