"""Feed-cache invalidation on the agent gateway.

The catalog feeds are cached in the GATEWAY's Redis DB, which Django's
cache backend cannot reach, and they survive gateway pod restarts. So
before this existed the only ways out of a stale feed were to wait out
FEED_FRESH_TTL (6h) or delete the keys by hand — a merchant's price
change took up to six hours to reach Google, Meta and TikTok. Observed
on staging: the feeds served 7 items well after the catalogue held 35.

That makes the "never raise" contract the important part: this is called
from admin actions and a management command, and an unreachable sidecar
must degrade to a reported error rather than take the whole purge down.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
import requests

from core.cache import gateway
from core.cache.registry import get_surface


@pytest.fixture
def configured(settings):
    """Both halves of the gateway wiring present.

    pytest-django's ``settings`` fixture rather than
    ``@override_settings``: the latter only decorates SimpleTestCase
    subclasses, and these are plain pytest classes.
    """
    settings.AGENT_GATEWAY_INTERNAL_URL = "http://gateway.internal:8080"
    settings.AGENT_GATEWAY_INTERNAL_SECRET = "shared-secret"
    settings.AGENT_GATEWAY_HTTP_TIMEOUT = 5
    return settings


class _Response:
    def __init__(self, payload=None, exc=None):
        self._payload = payload if payload is not None else {}
        self._exc = exc

    def raise_for_status(self):
        if self._exc:
            raise self._exc

    def json(self):
        return self._payload


class TestConfiguration:
    def test_not_configured_without_a_url(self, settings):
        settings.AGENT_GATEWAY_INTERNAL_URL = ""
        settings.AGENT_GATEWAY_INTERNAL_SECRET = "x"

        assert not gateway.is_configured()

    def test_not_configured_without_a_secret(self, settings):
        settings.AGENT_GATEWAY_INTERNAL_URL = "http://x"
        settings.AGENT_GATEWAY_INTERNAL_SECRET = ""

        assert not gateway.is_configured()

    def test_configured_with_both(self, configured):
        assert gateway.is_configured()

    def test_unconfigured_is_a_no_op_not_an_error(self, settings):
        """A deployment without the gateway must not have its cache
        purges fail."""
        settings.AGENT_GATEWAY_INTERNAL_URL = ""
        settings.AGENT_GATEWAY_INTERNAL_SECRET = ""

        result = gateway.invalidate_feeds(schema_name="webside")

        assert result.removed == 0
        assert "not configured" in (result.error or "")


class TestInvalidate:
    def test_posts_the_schema_and_the_shared_token(self, configured):
        with patch(
            "core.cache.gateway.requests.post",
            return_value=_Response({"removed": 8}),
        ) as post:
            result = gateway.invalidate_feeds(schema_name="webside")

        assert result.removed == 8
        assert result.error is None
        url, kwargs = post.call_args[0][0], post.call_args.kwargs
        assert url.endswith("/internal/feeds/invalidate")
        assert kwargs["json"] == {"schemaName": "webside"}
        assert kwargs["headers"]["X-Internal-Token"] == "shared-secret"

    def test_transport_failure_is_reported_not_raised(self, configured):
        """Called from admin actions — an unreachable gateway must not
        abort the rest of the purge."""
        with patch(
            "core.cache.gateway.requests.post",
            side_effect=requests.ConnectionError("refused"),
        ):
            result = gateway.invalidate_feeds(schema_name="webside")

        assert result.removed == 0
        assert "refused" in (result.error or "")

    def test_http_error_is_reported_not_raised(self, configured):
        with patch(
            "core.cache.gateway.requests.post",
            return_value=_Response(exc=requests.HTTPError("503")),
        ):
            result = gateway.invalidate_feeds(schema_name="webside")

        assert result.removed == 0
        assert result.error

    def test_junk_body_is_reported_not_raised(self, configured):
        """A 2xx whose body will not parse: the keys are almost certainly
        gone, but do not claim a count we do not have."""

        class _Junk(_Response):
            def json(self):
                raise ValueError("not json")

        with patch("core.cache.gateway.requests.post", return_value=_Junk()):
            result = gateway.invalidate_feeds(schema_name="webside")

        assert result.removed == 0
        assert result.error

    @pytest.mark.django_db
    def test_defaults_to_the_active_schema(self, configured):
        """A merchant purge runs inside schema_context, so the active
        connection already names the tenant."""
        with patch(
            "core.cache.gateway.requests.post",
            return_value=_Response({"removed": 0}),
        ) as post:
            gateway.invalidate_feeds()

        assert "schemaName" in post.call_args.kwargs["json"]
        assert post.call_args.kwargs["json"]["schemaName"]


class TestSurfaceWiring:
    def test_catalogue_surfaces_invalidate_the_feeds(self):
        """The feeds embed product rows and the category names used for
        g:product_type, so both surfaces must reach them."""
        for code in ("products", "categories"):
            assert get_surface(code).invalidates_gateway_feeds, code

    def test_unrelated_surfaces_do_not(self):
        """Purging the blog or the settings must not cost a gateway
        round trip or drop the feeds."""
        for code in ("blog", "settings", "page_config", "loyalty"):
            assert not get_surface(code).invalidates_gateway_feeds, code


class TestServiceIntegration:
    """The flag is only useful if ``_purge_surface`` acts on it."""

    def test_purging_a_flagged_surface_invalidates_the_feeds(self):
        from core.cache.registry import CacheSurface
        from core.cache.service import CacheService

        surface = CacheSurface(
            code="feedy",
            label="Feedy",
            description="",
            invalidates_gateway_feeds=True,
        )

        with patch(
            "core.cache.service.gateway_client.invalidate_feeds",
            return_value=gateway.GatewayPurgeResult(removed=8),
        ) as invalidate:
            result = CacheService._purge_surface(surface, dry_run=False)

        invalidate.assert_called_once()
        assert result.gateway_removed == 8
        assert result.gateway_error is None

    def test_unflagged_surface_never_calls_the_gateway(self):
        """Every purge would otherwise pay a cross-service round trip."""
        from core.cache.registry import CacheSurface
        from core.cache.service import CacheService

        surface = CacheSurface(code="plain", label="Plain", description="")

        with patch(
            "core.cache.service.gateway_client.invalidate_feeds"
        ) as invalidate:
            CacheService._purge_surface(surface, dry_run=False)

        invalidate.assert_not_called()

    def test_dry_run_does_not_touch_the_gateway(self):
        """There is no dry-run form of the endpoint — it deletes or it
        does not — so a dry run must not delete anything."""
        from core.cache.registry import CacheSurface
        from core.cache.service import CacheService

        surface = CacheSurface(
            code="feedy",
            label="Feedy",
            description="",
            invalidates_gateway_feeds=True,
        )

        with patch(
            "core.cache.service.gateway_client.invalidate_feeds"
        ) as invalidate:
            CacheService._purge_surface(surface, dry_run=True)

        invalidate.assert_not_called()

    def test_gateway_error_is_recorded_not_raised(self):
        from core.cache.registry import CacheSurface
        from core.cache.service import CacheService

        surface = CacheSurface(
            code="feedy",
            label="Feedy",
            description="",
            invalidates_gateway_feeds=True,
        )

        with patch(
            "core.cache.service.gateway_client.invalidate_feeds",
            return_value=gateway.GatewayPurgeResult(removed=0, error="boom"),
        ):
            result = CacheService._purge_surface(surface, dry_run=False)

        assert result.gateway_error == "boom"
