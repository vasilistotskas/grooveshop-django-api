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
    def _tenant(self, schema="media_flush_tenant"):
        from tenant.models import Tenant

        t = Tenant(
            schema_name=schema,
            name="Media Flush Tenant",
            slug="media-flush-tenant",
            owner_email="owner-media-flush@example.com",
        )
        t.auto_create_schema = False
        t.save()
        return t

    def test_suspend_dispatches_media_flush(self):
        from tenant.lifecycle import suspend_tenant
        from tenant.models import SuspendedReason

        tenant = self._tenant()
        with mock.patch("tenant.tasks.flush_tenant_media_task.delay") as delay:
            assert (
                suspend_tenant(tenant, reason=SuspendedReason.BILLING) is True
            )
        delay.assert_called_once_with("media_flush_tenant")

    def test_noop_resuspend_does_not_dispatch(self):
        from tenant.lifecycle import suspend_tenant
        from tenant.models import SuspendedReason

        tenant = self._tenant("media_flush_resuspend")
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

        tenant = self._tenant("media_flush_broker_down")
        with mock.patch(
            "tenant.tasks.flush_tenant_media_task.delay",
            side_effect=RuntimeError("broker down"),
        ):
            # Suspension still succeeds despite the dispatch failing.
            assert suspend_tenant(tenant, reason=SuspendedReason.MANUAL) is True
        tenant.refresh_from_db()
        assert tenant.is_active is False
