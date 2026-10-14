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


def _store(bind_tenant, schema, *, is_demo=False, **setting_values):
    bind_tenant(
        store_tenant(
            schema,
            mydata_user_id="store-user",
            mydata_subscription_key="k",
            is_demo=is_demo,
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


@pytest.mark.parametrize("environment", ["dev", "prod"])
@pytest.mark.parametrize("ground", ["", "   "])
def test_a_store_taking_real_orders_is_never_ready_without_the_ground(
    bind_tenant, settings, ground, environment
):
    """Retail receipts come from a fiscal device or a licensed provider
    (ν.4308/2014 art. 12 §8, §10); the ERP channel is only for a business
    exempt from the ΦΗΜ. A real store on the production deployment,
    switched on with credentials and automatic issuing, issues nothing
    without the recorded ground — in the sandbox too, because the setting
    defaults to the sandbox and its customers would get test receipts."""
    settings.REAL_ORDERS_DEPLOYMENT = True
    _store(
        bind_tenant,
        "mydata_cfg_no_ground",
        MYDATA_ENABLED=("bool", "value_bool", True),
        MYDATA_FHM_EXEMPTION_GROUND=("string", "value_string", ground),
        MYDATA_AUTO_SUBMIT=("bool", "value_bool", True),
        MYDATA_ENVIRONMENT=("string", "value_string", environment),
    )

    config = load_config()

    assert config.fhm_exemption_ground == ""
    assert config.takes_real_orders
    assert not config.issues_to_sandbox_only
    assert not config.is_ready()
    assert not config.issues_automatically()


@pytest.mark.parametrize(
    ("real_orders_deployment", "is_demo"),
    [(True, True), (False, False)],
    ids=["a demo store in production", "any store outside production"],
)
def test_a_store_taking_no_real_orders_may_issue_to_the_sandbox(
    bind_tenant, settings, real_orders_deployment, is_demo
):
    """AADE's sandbox registers test documents only, so a store that takes
    no real orders needs no ground to issue there."""
    settings.REAL_ORDERS_DEPLOYMENT = real_orders_deployment
    _store(
        bind_tenant,
        "mydata_cfg_sandbox",
        is_demo=is_demo,
        MYDATA_ENABLED=("bool", "value_bool", True),
        MYDATA_AUTO_SUBMIT=("bool", "value_bool", True),
        MYDATA_ENVIRONMENT=("string", "value_string", "dev"),
    )

    config = load_config()

    assert config.issues_to_sandbox_only
    assert config.is_ready()
    assert config.issues_automatically()


def test_a_demo_store_needs_the_ground_for_aade_production(
    bind_tenant, settings
):
    """The exception is the sandbox, not the store: a demo store sending to
    AADE's production environment issues real documents."""
    settings.REAL_ORDERS_DEPLOYMENT = True
    _store(
        bind_tenant,
        "mydata_cfg_demo_prod",
        is_demo=True,
        MYDATA_ENABLED=("bool", "value_bool", True),
        MYDATA_ENVIRONMENT=("string", "value_string", "prod"),
    )

    config = load_config()

    assert not config.issues_to_sandbox_only
    assert not config.is_ready()


@pytest.mark.parametrize("environment", ["production", "PROD", ""])
def test_an_unknown_environment_is_refused_not_guessed(
    bind_tenant, environment
):
    """A typo must not quietly become the sandbox (and so skip the ground):
    an environment that is neither is not ready, even with the ground."""
    _store(
        bind_tenant,
        "mydata_cfg_bad_env",
        MYDATA_ENABLED=("bool", "value_bool", True),
        MYDATA_FHM_EXEMPTION_GROUND=("string", "value_string", _GROUND),
        MYDATA_ENVIRONMENT=("string", "value_string", environment),
    )

    config = load_config()

    assert config.environment is None
    assert not config.is_ready()
    with pytest.raises(ValueError, match="MYDATA_ENVIRONMENT"):
        _ = config.base_url


def test_the_exemption_ground_is_a_store_setting_that_ships_blank():
    defaults = {
        item["name"]: item for item in django_settings.EXTRA_SETTINGS_DEFAULTS
    }
    ground = defaults["MYDATA_FHM_EXEMPTION_GROUND"]
    assert ground["type"] == "string"
    assert ground["value"] == ""
