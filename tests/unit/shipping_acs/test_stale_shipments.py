"""Tests for the stale-shipment admin alert (``check_stale_acs_shipments``).

Background (prod 2026-07-11): non-terminal shipments poll forever with
no human signal — one parcel sat 3 days at the destination station
after a wrong-address delivery failure, another voucher stayed ``new``
for 50 days because the parcel was never handed over. The task emails
ADMINS a digest and claims each row via ``stale_alert_sent`` (mirroring
``check_low_stock_products``); the poll service re-arms the flag when
tracking moves again.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import AcsShipment
from shipping_acs.tasks import check_stale_acs_shipments

pytestmark = pytest.mark.django_db

_ADMINS = ["admin@example.com"]


def _stale_shipment(**overrides) -> AcsShipment:
    """A NEW shipment with a voucher whose last event is 5 days old."""
    defaults = {
        "voucher_no": overrides.pop("voucher_no", "9700000001"),
        "shipment_state": AcsShipmentState.NEW,
        "last_event_at": timezone.now() - timedelta(days=5),
    }
    defaults.update(overrides)
    return AcsShipmentFactory(**defaults)


@pytest.fixture
def admins_configured(settings):
    settings.ADMINS = _ADMINS


class TestCheckStaleAcsShipments:
    def test_alerts_and_claims_stale_shipment(self, admins_configured):
        shipment = _stale_shipment()

        with patch("django.core.mail.mail_admins") as mock_mail:
            result = check_stale_acs_shipments.run()

        assert result["alerted"] == 1
        assert result["ids"] == [shipment.id]
        assert mock_mail.called
        shipment.refresh_from_db()
        assert shipment.stale_alert_sent is True

    def test_never_polled_shipment_uses_created_at_fallback(
        self, admins_configured
    ):
        shipment = _stale_shipment(last_event_at=None)
        AcsShipment.objects.filter(pk=shipment.pk).update(
            created_at=timezone.now() - timedelta(days=5)
        )

        with patch("django.core.mail.mail_admins") as mock_mail:
            result = check_stale_acs_shipments.run()

        assert result["alerted"] == 1
        assert mock_mail.called

    def test_skips_fresh_terminal_pending_and_claimed(self, admins_configured):
        # Fresh movement — not stale.
        _stale_shipment(
            voucher_no="9700000002",
            last_event_at=timezone.now() - timedelta(hours=6),
        )
        # Terminal — poller already skips it, alerting is noise.
        _stale_shipment(
            voucher_no="9700000003",
            shipment_state=AcsShipmentState.DELIVERED,
        )
        # No voucher yet — creation problems have their own path.
        AcsShipmentFactory(
            shipment_state=AcsShipmentState.PENDING_CREATION,
            last_event_at=timezone.now() - timedelta(days=5),
        )
        # Already alerted — claim flag dedupes.
        _stale_shipment(voucher_no="9700000004", stale_alert_sent=True)

        with patch("django.core.mail.mail_admins") as mock_mail:
            result = check_stale_acs_shipments.run()

        assert result == {"alerted": 0}
        assert not mock_mail.called

    def test_alerts_stranded_mint_after_24h(self, admins_configured):
        """A shipment stuck in ``pending_creation`` for over a day
        means the voucher mint failed permanently (prod order 143
        stranded 10 days) — it must appear in the digest even though
        it has no voucher."""
        shipment = AcsShipmentFactory(
            shipment_state=AcsShipmentState.PENDING_CREATION
        )
        AcsShipment.objects.filter(pk=shipment.pk).update(
            created_at=timezone.now() - timedelta(days=2)
        )

        with patch("django.core.mail.mail_admins") as mock_mail:
            result = check_stale_acs_shipments.run()

        assert result["alerted"] == 1
        assert mock_mail.called
        shipment.refresh_from_db()
        assert shipment.stale_alert_sent is True

    def test_no_admins_releases_claim(self, settings):
        settings.ADMINS = []
        shipment = _stale_shipment()

        with patch("django.core.mail.mail_admins") as mock_mail:
            result = check_stale_acs_shipments.run()

        assert result == {"alerted": 0, "reason": "no_admins"}
        assert not mock_mail.called
        shipment.refresh_from_db()
        # Claim released so a future run (with ADMINS set) can alert.
        assert shipment.stale_alert_sent is False

    def test_email_failure_releases_claim(self, admins_configured):
        shipment = _stale_shipment()

        with patch(
            "django.core.mail.mail_admins", side_effect=OSError("smtp down")
        ):
            result = check_stale_acs_shipments.run()

        assert result["alerted"] == 0
        assert "smtp down" in result["error"]
        shipment.refresh_from_db()
        assert shipment.stale_alert_sent is False


class TestStaleFlagRearm:
    def test_poll_clears_flag_when_new_event_arrives(self, monkeypatch):
        """A fresh tracking event re-arms the staleness alert."""
        from shipping_acs import services
        from shipping_acs.services import AcsService

        event_time = timezone.now().replace(microsecond=0)

        class _MovingClient:
            billing_code = "TEST_BILLING"

            def tracking_summary(self, voucher_no):
                # shipment_status=1 keeps the state at NEW — no order
                # transition side-effects in this test.
                return {
                    "delivery_flag": 0,
                    "returned_flag": 0,
                    "shipment_status": 1,
                }

            def tracking_details(self, voucher_no):
                return [
                    {
                        "checkpoint_date_time": event_time.isoformat(),
                        "checkpoint_action": "ΠΑΡΑΛΑΒΗ",
                        "checkpoint_location": "ΑΘΗΝΑ",
                        "checkpoint_notes": "",
                    }
                ]

        monkeypatch.setattr(services, "AcsClient", _MovingClient)

        shipment = _stale_shipment(stale_alert_sent=True)
        result = AcsService.poll_shipment_tracking(shipment)

        assert result.stale_alert_sent is False
