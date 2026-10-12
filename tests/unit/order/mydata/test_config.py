"""myDATA credentials come from the tenant row, never from settings."""

from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from extra_settings.models import Setting

from order.mydata.config import load_config
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db


def test_the_credentials_are_the_tenants_own(bind_tenant):
    bind_tenant(
        store_tenant(
            "mydata_cfg",
            mydata_user_id="store-user",
            mydata_subscription_key="store-key",
        )
    )
    Setting.objects.update_or_create(
        name="MYDATA_ENABLED",
        defaults={"value_type": "bool", "value_bool": True},
    )

    config = load_config()

    assert config.user_id == "store-user"
    assert config.subscription_key == "store-key"
    assert config.is_ready()
    assert config.headers()["ocp-apim-subscription-key"] == "store-key"


def test_a_store_without_credentials_is_not_ready(bind_tenant):
    bind_tenant(store_tenant("mydata_cfg_empty"))
    Setting.objects.update_or_create(
        name="MYDATA_ENABLED",
        defaults={"value_type": "bool", "value_bool": True},
    )

    config = load_config()

    assert config.user_id == ""
    assert config.subscription_key == ""
    assert not config.is_ready()


def test_a_retired_setting_row_is_not_a_fallback(bind_tenant):
    """The old ``MYDATA_*`` rows may still exist until the contract release
    deletes them; nothing may read them."""
    bind_tenant(store_tenant("mydata_cfg_orphan"))
    for name, value in (
        ("MYDATA_USER_ID", "orphan-user"),
        ("MYDATA_SUBSCRIPTION_KEY", "orphan-key"),
    ):
        Setting.objects.update_or_create(
            name=name,
            defaults={"value_type": "string", "value_string": value},
        )

    config = load_config()

    assert config.user_id == ""
    assert config.subscription_key == ""


def test_the_credentials_are_no_longer_extra_settings_definitions():
    names = {item["name"] for item in django_settings.EXTRA_SETTINGS_DEFAULTS}
    assert "MYDATA_USER_ID" not in names
    assert "MYDATA_SUBSCRIPTION_KEY" not in names
