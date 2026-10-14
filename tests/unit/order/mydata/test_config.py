"""myDATA credentials come from the tenant row, never from settings."""

from __future__ import annotations

import pytest
from django.conf import settings as django_settings
from extra_settings.models import Setting

from order.mydata.config import load_config
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db

_GROUND = "ΠΟΛ.1002/2014 §4, confirmed by the store's accountant, 2026-10-14"


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
    Setting.objects.update_or_create(
        name="MYDATA_FHM_EXEMPTION_GROUND",
        defaults={"value_type": "string", "value_string": _GROUND},
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


def _store(bind_tenant, schema, **setting_values):
    bind_tenant(
        store_tenant(
            schema, mydata_user_id="store-user", mydata_subscription_key="k"
        )
    )
    for name, (value_type, field, value) in setting_values.items():
        Setting.objects.update_or_create(
            name=name, defaults={"value_type": value_type, field: value}
        )


def test_the_platform_issues_by_itself_only_with_auto_submit(bind_tenant):
    _store(
        bind_tenant,
        "mydata_cfg_auto",
        MYDATA_ENABLED=("bool", "value_bool", True),
        MYDATA_FHM_EXEMPTION_GROUND=("string", "value_string", _GROUND),
        MYDATA_AUTO_SUBMIT=("bool", "value_bool", True),
    )
    assert load_config().issues_automatically()

    Setting.objects.update_or_create(
        name="MYDATA_AUTO_SUBMIT",
        defaults={"value_type": "bool", "value_bool": False},
    )
    config = load_config()
    # Ready (staff can issue from the order) but nothing is automatic.
    assert config.is_ready()
    assert not config.issues_automatically()


def test_a_store_without_myDATA_issues_nothing_by_itself(bind_tenant):
    _store(
        bind_tenant,
        "mydata_cfg_off",
        MYDATA_AUTO_SUBMIT=("bool", "value_bool", True),
    )
    assert not load_config().issues_automatically()


def test_the_credit_documents_have_a_series_of_their_own(bind_tenant):
    _store(bind_tenant, "mydata_cfg_series")
    config = load_config()

    assert config.invoice_series_prefix == "GRVP"
    assert config.credit_series_prefix == "GRVC"
    assert config.credit_series_prefix != config.invoice_series_prefix

    Setting.objects.update_or_create(
        name="MYDATA_CREDIT_SERIES_PREFIX",
        defaults={"value_type": "string", "value_string": "STORE-C"},
    )
    assert load_config().credit_series_prefix == "STORE-C"


@pytest.mark.parametrize("ground", ["", "   "])
def test_without_a_recorded_fhm_exemption_the_store_is_never_ready(
    bind_tenant, ground
):
    """Retail receipts come from a fiscal device or a licensed provider
    (ν.4308/2014 art. 12 §8, §10); the ERP channel is only for a business
    exempt from the ΦΗΜ. Switched on, with credentials and automatic
    issuing, a store that has not recorded that ground issues nothing."""
    _store(
        bind_tenant,
        "mydata_cfg_no_ground",
        MYDATA_ENABLED=("bool", "value_bool", True),
        MYDATA_FHM_EXEMPTION_GROUND=("string", "value_string", ground),
        MYDATA_AUTO_SUBMIT=("bool", "value_bool", True),
    )

    config = load_config()

    assert config.fhm_exemption_ground == ""
    assert not config.is_ready()
    assert not config.issues_automatically()


def test_the_exemption_ground_is_a_store_setting_that_ships_blank():
    defaults = {
        item["name"]: item for item in django_settings.EXTRA_SETTINGS_DEFAULTS
    }
    ground = defaults["MYDATA_FHM_EXEMPTION_GROUND"]
    assert ground["type"] == "string"
    assert ground["value"] == ""
