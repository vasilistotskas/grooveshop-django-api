"""BoxNow fanout Celery tasks must skip cleanly (log + continue, not
crash) for a tenant with no BoxNow credentials.

BoxNow credentials are tenant-only (no settings fallback — see
``tenant/credentials.py:box_now_credentials()``), so an unconfigured
tenant must never reach ``BoxNowClient()`` (which would raise
``BoxNowConfigError``) inside a fanout task dispatched to every active
tenant regardless of which carriers they've actually configured.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.django_db


def test_sync_boxnow_lockers_skips_when_unconfigured():
    from shipping_boxnow.tasks import sync_boxnow_lockers

    with patch(
        "shipping_boxnow.services.BoxNowService.sync_lockers"
    ) as mock_sync:
        result = sync_boxnow_lockers.run()

    assert result == {"created": 0, "updated": 0, "deactivated": 0}
    mock_sync.assert_not_called()


def test_sync_boxnow_lockers_runs_when_configured(boxnow_configured_tenant):
    from shipping_boxnow.tasks import sync_boxnow_lockers

    with patch(
        "shipping_boxnow.services.BoxNowService.sync_lockers",
        return_value={"created": 1, "updated": 0, "deactivated": 0},
    ) as mock_sync:
        result = sync_boxnow_lockers.run()

    assert result["created"] == 1
    mock_sync.assert_called()


def test_poll_boxnow_tracking_batch_skips_when_unconfigured():
    from shipping_boxnow.tasks import poll_boxnow_tracking_batch

    with patch("django.core.cache.cache.add") as mock_add:
        result = poll_boxnow_tracking_batch.run()

    assert result == {"dispatched": 0, "skipped": True}
    mock_add.assert_not_called()
