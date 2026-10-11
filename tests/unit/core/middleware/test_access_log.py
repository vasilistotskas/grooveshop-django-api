"""``AccessLogMiddleware``: one INFO line per request, with its latency.

The issue it closes (#142): the API had no per-request line carrying a
duration, so a slow storefront request could not be followed into
Django. The cases below go through the real middleware stack
(``client``), because what makes the line useful is its position in
``MIDDLEWARE`` — behind the probe bypass, inside the correlation id —
not just the body of one class.
"""

from __future__ import annotations

import json
import logging
import re
from unittest.mock import Mock, patch

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from core.logging import CorrelationIdFilter, JsonFormatter
from core.middleware.access_log import UNMATCHED_ROUTE, AccessLogMiddleware
from core.middleware.correlation_id import CORRELATION_ID_HEADER
from tenant.internal import INTERNAL_DOMAINS_PATH

ACCESS_LOGGER = "core.middleware.access_log"
PRODUCT_DETAIL_ROUTE = "api/v1/product/<int:pk>"


@pytest.fixture
def access_lines(caplog):
    """The access records, stamped the way the production handler does.

    ``CorrelationIdFilter`` is a HANDLER filter in ``LOGGING``, so a bare
    caplog handler would not see ``correlation_id`` at all.
    """
    caplog.handler.addFilter(CorrelationIdFilter())
    caplog.set_level(logging.INFO, logger=ACCESS_LOGGER)

    def lines() -> list[logging.LogRecord]:
        return [r for r in caplog.records if r.name == ACCESS_LOGGER]

    return lines


def _as_json(record: logging.LogRecord) -> dict:
    """What VictoriaLogs receives for ``record``."""
    return json.loads(JsonFormatter().format(record))


@pytest.mark.django_db
class TestOneLinePerRequest:
    def test_the_line_carries_method_route_status_duration_and_cid(
        self, client, access_lines
    ):
        response = client.get(
            "/api/v1/product/999999", headers={CORRELATION_ID_HEADER: "cid-42"}
        )

        (record,) = access_lines()
        payload = _as_json(record)
        assert record.levelno == logging.INFO
        assert payload["method"] == "GET"
        assert payload["route"] == PRODUCT_DETAIL_ROUTE
        assert payload["status"] == response.status_code == 404
        assert isinstance(payload["duration_ms"], int | float)
        assert payload["duration_ms"] >= 0
        assert payload["correlation_id"] == "cid-42"
        assert payload["message"] == (
            f"GET {PRODUCT_DETAIL_ROUTE} 404 {payload['duration_ms']:.1f}ms"
        )

    def test_the_line_and_the_response_header_share_one_id(
        self, client, access_lines
    ):
        """No inbound id: the one Django mints is on the line AND echoed
        to the caller, so the two can be matched."""
        response = client.get("/api/v1/product/999999")

        (record,) = access_lines()
        minted = response[CORRELATION_ID_HEADER]
        assert re.fullmatch(r"[0-9a-f]{32}", minted)
        assert _as_json(record)["correlation_id"] == minted

    def test_the_route_is_the_pattern_never_the_raw_path(
        self, client, access_lines
    ):
        client.get("/api/v1/product/999999?email=a@b.gr&token=hunter2")

        (record,) = access_lines()
        line = json.dumps(_as_json(record))
        assert "999999" not in line
        assert "hunter2" not in line
        assert "a@b.gr" not in line

    def test_a_request_matching_no_route_gets_a_fixed_label(
        self, client, access_lines
    ):
        client.get("/wp-login-9d8f7a.php")

        (record,) = access_lines()
        payload = _as_json(record)
        assert payload["route"] == UNMATCHED_ROUTE
        assert payload["status"] == 404
        assert "wp-login" not in json.dumps(payload)

    @override_settings(AGENT_GATEWAY_INTERNAL_SECRET="internal-secret")
    def test_a_request_a_middleware_answers_is_named_by_its_route(
        self, client, access_lines
    ):
        """``InternalDomainsMiddleware`` serves the feed before any view
        runs, so ``resolver_match`` is empty; the line still names the
        route, never ``unmatched``."""
        response = client.get(
            INTERNAL_DOMAINS_PATH,
            headers={"X-Internal-Token": "internal-secret"},
        )

        (record,) = access_lines()
        payload = _as_json(record)
        assert response.status_code == 200
        assert payload["route"] == INTERNAL_DOMAINS_PATH.lstrip("/")

    def test_a_cors_preflight_names_the_route_not_the_path(
        self, client, access_lines
    ):
        client.options(
            "/api/v1/product/999999",
            headers={
                "Origin": "https://example.test",
                "Access-Control-Request-Method": "GET",
            },
        )

        (record,) = access_lines()
        payload = _as_json(record)
        assert payload["route"] == PRODUCT_DETAIL_ROUTE
        assert "999999" not in json.dumps(payload)

    def test_the_kubelet_readiness_probe_is_not_logged(
        self, client, access_lines
    ):
        response = client.get("/api/v1/health/live")

        assert response.status_code == 200
        assert access_lines() == []

    async def test_it_logs_under_the_async_stack(
        self, async_client, access_lines
    ):
        """Served as ASGI, a sync-only middleware is adapted by Django;
        the line must come out the same."""
        response = await async_client.get(
            "/wp-login-9d8f7a.php", headers={CORRELATION_ID_HEADER: "cid-async"}
        )

        (record,) = access_lines()
        payload = _as_json(record)
        assert payload["status"] == response.status_code == 404
        assert payload["correlation_id"] == "cid-async"


class TestMeasurement:
    @patch("core.middleware.access_log.time.perf_counter")
    def test_duration_is_milliseconds_rounded_to_a_tenth(
        self, perf_counter, access_lines
    ):
        perf_counter.side_effect = [10.0, 10.01234]
        middleware = AccessLogMiddleware(lambda request: HttpResponse("ok"))

        middleware(RequestFactory().get("/anything"))

        (record,) = access_lines()
        assert record.fields["duration_ms"] == 12.3

    def test_the_response_is_returned_untouched(self, access_lines):
        response = HttpResponse("ok", status=201)
        middleware = AccessLogMiddleware(Mock(return_value=response))

        assert middleware(RequestFactory().post("/anything")) is response
        (record,) = access_lines()
        assert record.fields["method"] == "POST"
        assert record.fields["status"] == 201


class TestPlacementInMiddleware:
    """The position is the contract, so pin it. The module docstring
    gives the reason for each neighbour."""

    def test_it_sits_behind_the_probe_bypass_and_inside_the_cid(self):
        order = settings.MIDDLEWARE
        probe = order.index(
            "core.middleware.health_probe.HealthProbeMiddleware"
        )
        cid = order.index(
            "core.middleware.correlation_id.CorrelationIdMiddleware"
        )
        access = order.index("core.middleware.access_log.AccessLogMiddleware")

        assert probe < cid < access

    def test_it_sits_above_tenant_resolution(self):
        """``TenantMainMiddleware`` is stripped from the unit-test
        settings, so read the declared order from the settings module
        itself rather than ``settings.MIDDLEWARE``."""
        import settings as project_settings

        order = project_settings.MIDDLEWARE
        tenant = order.index(
            "django_tenants.middleware.main.TenantMainMiddleware"
        )
        access = order.index("core.middleware.access_log.AccessLogMiddleware")

        assert access < tenant
