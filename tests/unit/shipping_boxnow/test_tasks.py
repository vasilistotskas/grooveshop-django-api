"""Unit tests for shipping_boxnow Celery tasks."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from shipping_boxnow.tasks import process_boxnow_webhook_event


@pytest.mark.django_db
def test_webhook_apply_failure_alerts_admins():
    """A non-retryable failure while applying a verified webhook must
    page admins, not silently drop the event. The HTTP view already
    returned 200 (BoxNow won't retry), so an unhandled payload shape
    would otherwise lose a possibly-state-changing event with no signal.
    """
    envelope = {"id": "msg-boom", "data": {"parcelId": "p1"}}

    with (
        patch(
            "shipping_boxnow.services.BoxNowService.apply_webhook_event",
            side_effect=KeyError("event"),
        ),
        patch(
            "shipping.alerts.alert_admins_webhook_processing_failed"
        ) as mock_alert,
    ):
        result = process_boxnow_webhook_event.apply(args=[envelope]).get()

    assert result["status"] == "error"
    mock_alert.assert_called_once()
    assert mock_alert.call_args.kwargs["carrier"] == "BoxNow"
    assert mock_alert.call_args.kwargs["message_id"] == "msg-boom"


@pytest.mark.django_db
def test_webhook_retryable_error_is_not_swallowed():
    """A retryable error must propagate to Celery's autoretry, never hit
    the alert path (which is for permanent/malformed failures only). In
    eager mode Celery surfaces the retry as ``celery.exceptions.Retry``."""
    from celery.exceptions import Retry

    from shipping_boxnow.exceptions import BoxNowRetryableError

    envelope = {"id": "msg-retry", "data": {"parcelId": "p1"}}

    with (
        patch(
            "shipping_boxnow.services.BoxNowService.apply_webhook_event",
            side_effect=BoxNowRetryableError("boom"),
        ),
        patch(
            "shipping.alerts.alert_admins_webhook_processing_failed"
        ) as mock_alert,
    ):
        with pytest.raises(Retry):
            process_boxnow_webhook_event.apply(args=[envelope]).get()

    mock_alert.assert_not_called()
