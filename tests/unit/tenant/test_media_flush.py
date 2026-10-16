"""Suspending (or destroying) a tenant flushes its media-stream cache.

Processed images are keyed by ``image:{schema}`` and live for their TTL
(180/360 days); the serve path never re-checks tenant existence. So a
suspension must actively evict them via media-stream's
``/admin/cache/flush-tenant`` — best-effort and off the critical path.
"""

from __future__ import annotations

from unittest import mock

import pytest
import requests

from tenant.media_flush import flush_tenant_media
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db


@pytest.fixture
def _configured(settings):
    settings.MEDIA_STREAM_INTERNAL_URL = "http://media-stream-service:80"
    settings.MEDIA_STREAM_INTERNAL_SECRET = "internal-secret"


class TestFlushTenantMedia:
    def test_posts_flush_with_schema_and_secret(self, _configured):
        captured = {}

        def _fake_post(endpoint, json=None, headers=None, timeout=None):
            captured["endpoint"] = endpoint
            captured["json"] = json
            captured["headers"] = headers
            resp = mock.Mock()
            resp.raise_for_status = mock.Mock()
            return resp

        with mock.patch(
            "tenant.media_flush.requests.post", side_effect=_fake_post
        ):
            assert flush_tenant_media("acme") is True

        assert captured["endpoint"] == (
            "http://media-stream-service:80/admin/cache/flush-tenant"
        )
        assert captured["json"] == {"tenantSchema": "acme"}
        assert captured["headers"]["x-internal-secret"] == "internal-secret"

    def test_skips_when_unconfigured(self, settings):
        settings.MEDIA_STREAM_INTERNAL_URL = ""
        settings.MEDIA_STREAM_INTERNAL_SECRET = ""
        with mock.patch("tenant.media_flush.requests.post") as post:
            assert flush_tenant_media("acme") is False
            post.assert_not_called()

    def test_raises_on_http_error_for_task_retry(self, _configured):
        with (
            mock.patch(
                "tenant.media_flush.requests.post",
                side_effect=requests.ConnectionError("media-stream down"),
            ),
            pytest.raises(requests.RequestException),
        ):
            flush_tenant_media("acme")


class TestSuspendDispatchesFlush:
    def test_suspend_dispatches_media_flush(self):
        from tenant.lifecycle import suspend_tenant
        from tenant.models import SuspendedReason

        tenant = store_tenant("media_flush_tenant")
        with mock.patch("tenant.tasks.flush_tenant_media_task.delay") as delay:
            assert (
                suspend_tenant(tenant, reason=SuspendedReason.BILLING) is True
            )
        delay.assert_called_once_with("media_flush_tenant", [])

    def test_noop_resuspend_does_not_dispatch(self):
        from tenant.lifecycle import suspend_tenant
        from tenant.models import SuspendedReason

        tenant = store_tenant("media_flush_resuspend")
        suspend_tenant(tenant, reason=SuspendedReason.MANUAL)
        # Already suspended — a second call is a no-op and must not flush.
        with mock.patch("tenant.tasks.flush_tenant_media_task.delay") as delay:
            assert (
                suspend_tenant(tenant, reason=SuspendedReason.BILLING) is False
            )
        delay.assert_not_called()

    def test_dispatch_failure_does_not_break_suspend(self):
        from tenant.lifecycle import suspend_tenant
        from tenant.models import SuspendedReason

        tenant = store_tenant("media_flush_broker_down")
        with mock.patch(
            "tenant.tasks.flush_tenant_media_task.delay",
            side_effect=RuntimeError("broker down"),
        ):
            # Suspension still succeeds despite the dispatch failing.
            assert suspend_tenant(tenant, reason=SuspendedReason.MANUAL) is True
        tenant.refresh_from_db()
        assert tenant.is_active is False


PLAN = [{"zone_id": "a" * 32, "prefixes": ["assets.example/m/acme/"]}]


class TestFlushTaskPurgesTheEdgeAfterTheFlush:
    def test_the_edge_is_purged_only_after_media_stream_flushed(self):
        from tenant.tasks import flush_tenant_media_task

        order = []
        with (
            mock.patch(
                "tenant.media_flush.flush_tenant_media",
                side_effect=lambda schema: order.append("flush") or True,
            ),
            mock.patch(
                "core.cache.edge.purge_media",
                side_effect=lambda schema, plan: order.append("purge"),
            ) as purge,
        ):
            result = flush_tenant_media_task.run("acme", PLAN)

        assert order == ["flush", "purge"]
        purge.assert_called_once_with("acme", PLAN)
        assert result == {"flushed": True, "edge_zones": 1}

    def test_a_failed_flush_never_reaches_the_edge(self):
        from tenant.tasks import flush_tenant_media_task

        with (
            mock.patch(
                "tenant.media_flush.flush_tenant_media",
                side_effect=requests.ConnectionError("down"),
            ),
            mock.patch("core.cache.edge.purge_media") as purge,
            pytest.raises(requests.RequestException),
        ):
            flush_tenant_media_task.run("acme", PLAN)
        purge.assert_not_called()

    def test_a_task_dispatched_without_a_plan_still_flushes(self):
        from tenant.tasks import flush_tenant_media_task

        with (
            mock.patch(
                "tenant.media_flush.flush_tenant_media", return_value=False
            ),
            mock.patch("core.cache.edge.purge_media") as purge,
        ):
            result = flush_tenant_media_task.run("acme")
        purge.assert_called_once_with("acme", [])
        assert result == {"flushed": False, "edge_zones": 0}


class TestLifecyclePlansTheEdgePurge:
    @pytest.fixture(autouse=True)
    def _platform(self, settings):
        settings.CLOUDFLARE_PLATFORM_ZONE_NAME = "grooveshop.space"
        settings.CLOUDFLARE_PLATFORM_ZONE_ID = "a" * 32
        settings.CLOUDFLARE_PLATFORM_API_TOKEN = "platform-token"
        settings.MEDIA_STREAM_BASE_URL = "https://assets.grooveshop.space"

    def test_suspend_hands_the_task_zone_ids_never_a_token(self):
        from tenant.lifecycle import suspend_tenant
        from tenant.models import SuspendedReason

        tenant = store_tenant(
            "edge_suspend",
            cloudflare_zone_id="b" * 32,
            cloudflare_api_token="own-token-secret",
        )
        with mock.patch("tenant.tasks.flush_tenant_media_task.delay") as delay:
            suspend_tenant(tenant, reason=SuspendedReason.BILLING)

        schema, plan = delay.call_args.args
        assert schema == "edge_suspend"
        assert plan == [
            {
                "zone_id": "a" * 32,
                "prefixes": [
                    "assets.grooveshop.space/media_stream-image/media/edge_suspend/"
                ],
            }
        ]
        assert "own-token-secret" not in repr(delay.call_args)

    def test_destroy_plans_before_the_row_and_its_domains_are_gone(self):
        from datetime import timedelta

        from django.utils import timezone

        from tenant.lifecycle import destroy_tenant
        from tenant.models import Tenant, TenantDomain

        tenant = store_tenant(
            "edge_destroy",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=25),
        )
        TenantDomain.objects.create(
            domain="assets.edge-destroy.grooveshop.space",
            tenant=tenant,
            is_primary=False,
        )
        with (
            mock.patch("tenant.tasks.flush_tenant_media_task.delay") as delay,
            mock.patch.object(
                Tenant,
                "delete",
                autospec=True,
                side_effect=lambda t, **kw: t.domains.all().delete(),
            ),
            mock.patch("tenant.offboarding.purge_search_indexes"),
            mock.patch("tenant.offboarding.purge_tenant_files"),
        ):
            destroy_tenant(tenant)

        assert not TenantDomain.objects.filter(tenant=tenant).exists()
        _schema, plan = delay.call_args.args
        assert plan[0]["prefixes"] == [
            "assets.edge-destroy.grooveshop.space/media_stream-image/media/edge_destroy/",
            "assets.grooveshop.space/media_stream-image/media/edge_destroy/",
        ]

    def test_an_unplannable_row_does_not_stop_the_suspension(self):
        from tenant.lifecycle import suspend_tenant
        from tenant.models import SuspendedReason

        tenant = store_tenant("edge_unplannable")
        with (
            mock.patch(
                "core.cache.edge.tenant_media_purges",
                side_effect=RuntimeError("boom"),
            ),
            mock.patch("tenant.tasks.flush_tenant_media_task.delay") as delay,
        ):
            assert suspend_tenant(tenant, reason=SuspendedReason.MANUAL) is True
        delay.assert_called_once_with("edge_unplannable", [])
