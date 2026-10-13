"""BoxNow environment (stage vs production) is the tenant's own choice.

``Tenant.box_now_live_mode`` picks the API hosts; the platform only
carries both sets (``BOXNOW_PRODUCTION_*`` / ``BOXNOW_STAGE_*``). A
deployment named ``staging`` (``BOXNOW_LIVE_MODE_ALLOWED`` off) refuses
the production API outright, because staging mirrors the production
tenant rows and a stale live flag there must not book a real parcel.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from shipping_boxnow.client import BoxNowClient
from shipping_boxnow.exceptions import BoxNowConfigError
from tenant.credentials import box_now_credentials

PRODUCTION_API = "https://api-production.example.test"
PRODUCTION_LOCATIONS = "https://locationapi-production.example.test"
STAGE_API = "https://api-stage.example.test"
STAGE_LOCATIONS = "https://locationapi-stage.example.test"


@pytest.fixture(autouse=True)
def _platform_hosts(settings):
    settings.BOXNOW_PRODUCTION_API_BASE_URL = f"{PRODUCTION_API}/"
    settings.BOXNOW_PRODUCTION_LOCATION_API_BASE_URL = PRODUCTION_LOCATIONS
    settings.BOXNOW_STAGE_API_BASE_URL = STAGE_API
    settings.BOXNOW_STAGE_LOCATION_API_BASE_URL = f"{STAGE_LOCATIONS}/"
    settings.BOXNOW_LIVE_MODE_ALLOWED = True


def _bind(bind_tenant, **fields):
    bind_tenant(
        SimpleNamespace(
            schema_name="boxnow-env",
            box_now_client_id="cid",
            box_now_client_secret="secret",
            box_now_partner_id="777",
            box_now_webhook_secret="whs",
            **fields,
        )
    )


def _client() -> BoxNowClient:
    return BoxNowClient(session=MagicMock())


class TestUrlSelection:
    def test_live_tenant_uses_the_production_hosts(self, bind_tenant):
        _bind(bind_tenant, box_now_live_mode=True)

        client = _client()

        assert client.api_base_url == PRODUCTION_API
        assert client.location_api_base_url == PRODUCTION_LOCATIONS
        assert client.environment == "production"

    def test_non_live_tenant_uses_the_stage_hosts(self, bind_tenant):
        _bind(bind_tenant, box_now_live_mode=False)

        client = _client()

        assert client.api_base_url == STAGE_API
        assert client.location_api_base_url == STAGE_LOCATIONS
        assert client.environment == "stage"

    def test_an_unset_flag_means_stage_never_production(self, bind_tenant):
        _bind(bind_tenant)

        assert _client().environment == "stage"

    def test_two_tenants_in_one_process_get_their_own_environment(
        self, bind_tenant
    ):
        _bind(bind_tenant, box_now_live_mode=True)
        live = _client()
        _bind(bind_tenant, box_now_live_mode=False)
        demo = _client()

        assert live.api_base_url == PRODUCTION_API
        assert demo.api_base_url == STAGE_API

    def test_the_token_cache_is_scoped_by_environment(self, bind_tenant):
        _bind(bind_tenant, box_now_live_mode=True)
        live = _client()
        _bind(bind_tenant, box_now_live_mode=False)
        stage = _client()

        assert live.partner_id == stage.partner_id
        assert live._token_cache_key != stage._token_cache_key
        assert live._token_cache_key == "boxnow:access_token:production:777"
        assert stage._token_cache_key == "boxnow:access_token:stage:777"

    def test_the_legacy_platform_url_setting_is_not_read(
        self, bind_tenant, settings
    ):
        settings.BOXNOW_API_BASE_URL = "https://legacy.example.test"
        _bind(bind_tenant, box_now_live_mode=False)

        assert _client().api_base_url == STAGE_API


class TestStagingSafety:
    def test_a_deployment_that_forbids_live_refuses_a_live_tenant(
        self, bind_tenant, settings
    ):
        settings.BOXNOW_LIVE_MODE_ALLOWED = False
        _bind(bind_tenant, box_now_live_mode=True)

        with pytest.raises(BoxNowConfigError):
            _client()

    def test_a_deployment_that_forbids_live_still_serves_stage_tenants(
        self, bind_tenant, settings
    ):
        settings.BOXNOW_LIVE_MODE_ALLOWED = False
        _bind(bind_tenant, box_now_live_mode=False)

        assert _client().api_base_url == STAGE_API


class TestWebhookVerificationUnaffected:
    @pytest.mark.parametrize("live", [True, False])
    def test_the_webhook_secret_resolves_whatever_the_mode(
        self, bind_tenant, settings, live
    ):
        # The webhook view reads only ``webhook_secret``; it must keep
        # working on a deployment that refuses the live API.
        settings.BOXNOW_LIVE_MODE_ALLOWED = False
        _bind(bind_tenant, box_now_live_mode=live)

        assert box_now_credentials()["webhook_secret"] == "whs"
