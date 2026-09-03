"""Alerts for ACS vouchers with no printed label.

Background (prod 2026-09-03): ACS rejects the WHOLE pickup list when any
voucher on it is unprinted, and ``ACS_Issue_Pickup_List`` takes a pickup
date with no voucher list — there is no partial manifest to fall back
on. One order placed at 15:00 whose label had not been printed therefore
blocked six ready parcels from being manifested that day, and the only
signal was a log line nobody reads.

Two moments matter, and both name the exact orders because "print the
labels" is not actionable without the list:

* 15:45 — ``warn_unprinted_acs_vouchers``, while there is still time.
* 16:30 — the manifest was refused; say so instead of failing silently.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import AcsAPIError
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.tasks import (
    issue_daily_acs_pickup_list,
    warn_unprinted_acs_vouchers,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def admins_configured(settings):
    settings.ADMINS = [("Admin", "admin@example.com")]


def _candidate(voucher_no, *, printed=False):
    """A NEW shipment with a voucher and no pickup list."""
    from django.utils import timezone

    return AcsShipmentFactory(
        voucher_no=voucher_no,
        shipment_state=AcsShipmentState.NEW,
        label_printed_at=timezone.now() if printed else None,
    )


class TestWarnUnprintedAcsVouchers:
    def test_alerts_naming_only_the_unprinted_candidates(
        self, acs_configured_tenant, admins_configured
    ):
        unprinted = _candidate("9800000001")
        _candidate("9800000002", printed=True)

        with patch("django.core.mail.send_mail") as mock_mail:
            result = warn_unprinted_acs_vouchers.run()

        assert result["unprinted"] == 1
        assert result["alerted"] == 1
        assert mock_mail.called

        # The body has to carry the order number — that is what the
        # merchant searches for to print the label.
        body = mock_mail.call_args.kwargs["message"]
        assert "9800000001" in body
        assert str(unprinted.order_id) in body
        assert "9800000002" not in body

    def test_quiet_when_every_candidate_is_printed(
        self, acs_configured_tenant, admins_configured
    ):
        _candidate("9800000002", printed=True)

        with patch("django.core.mail.send_mail") as mock_mail:
            result = warn_unprinted_acs_vouchers.run()

        assert result["unprinted"] == 0
        assert not mock_mail.called

    def test_already_manifested_vouchers_are_not_candidates(
        self, acs_configured_tenant, admins_configured
    ):
        from shipping_acs.factories import AcsPickupListFactory

        _candidate("9800000003").__class__.objects.filter(
            voucher_no="9800000003"
        ).update(pickup_list=AcsPickupListFactory())

        with patch("django.core.mail.send_mail") as mock_mail:
            result = warn_unprinted_acs_vouchers.run()

        assert result["unprinted"] == 0
        assert not mock_mail.called

    def test_skips_a_tenant_without_acs_credentials(self, admins_configured):
        _candidate("9800000001")

        with patch("django.core.mail.send_mail") as mock_mail:
            result = warn_unprinted_acs_vouchers.run()

        assert result == {"status": "skipped_unconfigured"}
        assert not mock_mail.called


class TestPickupListRefusalAlert:
    @staticmethod
    def _refusal(vouchers):
        return AcsAPIError(
            alias="ACS_Issue_Pickup_List",
            error_message="Αδύνατη η έκδοση λίστας παραλαβής.",
            raw={
                "PickupList_No": None,
                "Unprinted_Found": len(vouchers),
                "Unprinted_Vouchers": vouchers,
            },
        )

    def test_alerts_and_still_fails_the_task(
        self, acs_configured_tenant, admins_configured
    ):
        blocking = _candidate("9800000001")

        with (
            patch(
                "shipping_acs.services.AcsService.issue_daily_pickup_list",
                side_effect=self._refusal(["9800000001"]),
            ),
            patch("django.core.mail.send_mail") as mock_mail,
            # The alert must not swallow the failure: a task that
            # reports success here is the exact bug this replaces.
            pytest.raises(AcsAPIError),
        ):
            issue_daily_acs_pickup_list.run()

        assert mock_mail.called
        body = mock_mail.call_args.kwargs["message"]
        assert "9800000001" in body
        assert str(blocking.order_id) in body
        # ACS's own words are the actionable part.
        assert "Αδύνατη η έκδοση" in body

    def test_acs_voucher_list_wins_over_the_local_flag(
        self, acs_configured_tenant, admins_configured
    ):
        # ACS is authoritative on what "printed" means — a label printed
        # from its own portal never reaches label_printed_at. When ACS
        # names the offenders, report those, not our local guess.
        acs_says = _candidate("9800000001", printed=True)
        _candidate("9800000002")

        with (
            patch(
                "shipping_acs.services.AcsService.issue_daily_pickup_list",
                side_effect=self._refusal(["9800000001"]),
            ),
            patch("django.core.mail.send_mail") as mock_mail,
            pytest.raises(AcsAPIError),
        ):
            issue_daily_acs_pickup_list.run()

        body = mock_mail.call_args.kwargs["message"]
        assert str(acs_says.order_id) in body
        assert "9800000002" not in body

    def test_a_mail_failure_does_not_mask_the_refusal(
        self, acs_configured_tenant, admins_configured
    ):
        _candidate("9800000001")

        with (
            patch(
                "shipping_acs.services.AcsService.issue_daily_pickup_list",
                side_effect=self._refusal(["9800000001"]),
            ),
            patch(
                "django.core.mail.send_mail",
                side_effect=OSError("smtp down"),
            ),
            pytest.raises(AcsAPIError),
        ):
            issue_daily_acs_pickup_list.run()

    def test_a_successful_issue_sends_nothing(
        self, acs_configured_tenant, admins_configured
    ):
        from shipping_acs.factories import AcsPickupListFactory

        pickup_list = AcsPickupListFactory()
        with (
            patch(
                "shipping_acs.services.AcsService.issue_daily_pickup_list",
                return_value=pickup_list,
            ),
            patch("django.core.mail.send_mail") as mock_mail,
        ):
            result = issue_daily_acs_pickup_list.run()

        assert result["status"] == "ok"
        assert not mock_mail.called
