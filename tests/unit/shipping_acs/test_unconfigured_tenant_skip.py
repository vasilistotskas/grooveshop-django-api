"""ACS fanout Celery tasks must skip cleanly (log + continue, not
crash) for a tenant with no ACS credentials.

ACS credentials are tenant-only (no settings fallback — see
``tenant/credentials.py:acs_credentials()``), so an unconfigured tenant
must never reach ``AcsClient()`` (which would raise ``AcsConfigError``)
inside a fanout task dispatched to every active tenant regardless of
which carriers they've actually configured.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.django_db


def test_sync_acs_stations_skips_when_unconfigured():
    from shipping_acs.tasks import sync_acs_stations

    with patch("shipping_acs.services.AcsService.sync_stations") as mock_sync:
        result = sync_acs_stations.run()

    assert result == {"upserted": 0, "deactivated": 0}
    mock_sync.assert_not_called()


def test_sync_acs_stations_runs_when_configured(acs_configured_tenant):
    from shipping_acs.tasks import sync_acs_stations

    with patch(
        "shipping_acs.services.AcsService.sync_stations",
        return_value={"upserted": 1, "deactivated": 0},
    ) as mock_sync:
        result = sync_acs_stations.run()

    assert result["upserted"] == 1
    mock_sync.assert_called()


def test_issue_daily_acs_pickup_list_skips_when_unconfigured():
    from shipping_acs.tasks import issue_daily_acs_pickup_list

    with patch(
        "shipping_acs.services.AcsService.issue_daily_pickup_list"
    ) as mock_issue:
        result = issue_daily_acs_pickup_list.run()

    assert result == {"status": "skipped_unconfigured"}
    mock_issue.assert_not_called()


def test_reconcile_acs_cod_payouts_skips_when_unconfigured():
    from shipping_acs.tasks import reconcile_acs_cod_payouts

    with patch(
        "shipping_acs.services.AcsService.reconcile_cod_payouts"
    ) as mock_reconcile:
        result = reconcile_acs_cod_payouts.run()

    assert result == {"upserted": 0, "linked": 0, "skipped": 0}
    mock_reconcile.assert_not_called()


def test_poll_acs_tracking_batch_skips_when_unconfigured():
    from shipping_acs.tasks import poll_acs_tracking_batch

    with patch("django.core.cache.cache.add") as mock_add:
        result = poll_acs_tracking_batch.run()

    assert result == {"dispatched": 0, "skipped": True}
    mock_add.assert_not_called()
