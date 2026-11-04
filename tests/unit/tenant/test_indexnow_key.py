"""A store's IndexNow key: made once, valid, and served only while on.

The storefront serves ``/<key>.txt`` from ``tenant/resolve``. The field must
be OPTIONAL in the schema (a read-only field is REQUIRED there, and a
storefront deployed before this backend would reject every resolve) and
empty unless the store switched IndexNow on, or the storefront would serve a
proof of ownership for a notification the store never sends.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError

from tenant.models import Tenant
from tenant.serializers import TenantConfigSerializer
from tenant.validators import INDEXNOW_KEY_PATTERN
from tests.unit.indexnow.conftest import setting_reader
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db


def _enabled(value: bool):
    return patch(
        "extra_settings.models.Setting.get",
        side_effect=setting_reader(INDEXNOW_ENABLED=value),
    )


def test_every_store_gets_its_own_valid_key():
    first = store_tenant("indexnow_first")
    second = store_tenant("indexnow_second")

    assert re.fullmatch(INDEXNOW_KEY_PATTERN, first.indexnow_key)
    assert first.indexnow_key != second.indexnow_key


@pytest.mark.parametrize(
    "key", ["short", "has space1", "slash/in/key1", "x" * 129]
)
def test_a_key_the_protocol_refuses_is_refused(key):
    tenant = store_tenant("indexnow_bad")
    tenant.indexnow_key = key

    with pytest.raises(ValidationError) as error:
        tenant.clean_fields(exclude=[])

    assert "indexnow_key" in error.value.message_dict


def test_the_key_is_served_while_the_store_has_indexnow_on():
    tenant = store_tenant("indexnow_on")

    with _enabled(True):
        data = TenantConfigSerializer(tenant).data

    assert data["indexnow_key"] == tenant.indexnow_key


def test_no_key_is_served_while_the_store_has_indexnow_off():
    tenant = store_tenant("indexnow_off")

    with _enabled(False):
        data = TenantConfigSerializer(tenant).data

    assert data["indexnow_key"] == ""


def test_the_control_plane_serves_no_key():
    tenant = Tenant(
        schema_name="public", name="p", slug="p", owner_email="p@example.com"
    )

    with _enabled(True):
        assert tenant.published_indexnow_key == ""


def test_the_field_is_optional_in_the_schema():
    field = TenantConfigSerializer().fields["indexnow_key"]

    assert not field.read_only
    assert not field.required
