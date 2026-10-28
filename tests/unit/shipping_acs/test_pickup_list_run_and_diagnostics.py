"""The pickup-list run record, the shipment timeline and the new admins."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.utils import timezone

from shipping_acs.admin import (
    AcsApiCallAdmin,
    AcsPickupListRunAdmin,
    AcsShipmentAdmin,
)
from shipping_acs.diagnostics import shipment_timeline
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import AcsAPIError, AcsUnprintedVouchersError
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.models import (
    AcsApiCall,
    AcsPickupListRun,
    AcsShipment,
)
from shipping_acs.models.pickup_list_run import AcsPickupListRunOutcome
from shipping_acs.services import AcsService
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _candidate(voucher_no, *, printed=False):
    return AcsShipmentFactory(
        voucher_no=voucher_no,
        shipment_state=AcsShipmentState.NEW,
        label_printed_at=timezone.now() if printed else None,
    )


def _acs_returns(monkeypatch, payload, *, lists=None, vouchers=None):
    from shipping_acs import services

    class _Client:
        billing_code = "TEST_BILLING"

        def issue_pickup_list(self, *, pickup_date):
            return payload

        def get_pickup_lists(self, *, pickup_date):
            return lists or []

        def get_pickup_list_vouchers(self, *, pickup_list_no, pickup_date):
            return (vouchers or {}).get(pickup_list_no, [])

    monkeypatch.setattr(services, "AcsClient", _Client)


SILENT = {
    "PickupList_No": None,
    "Unprinted_Found": 0,
    "Error_Message": None,
    "Unprinted_Vouchers": [],
}


class TestRunRecord:
    def test_issued(self, monkeypatch):
        shipment = _candidate("7001", printed=True)
        _acs_returns(
            monkeypatch,
            {"PickupList_No": "PL1", "Unprinted_Found": 0},
            vouchers={"PL1": ["7001"]},
        )

        pickup_list = AcsService.issue_daily_pickup_list(issued_by_id=5)

        run = AcsPickupListRun.objects.get()
        assert run.outcome == AcsPickupListRunOutcome.ISSUED
        assert run.pickup_list_id == pickup_list.pk
        assert run.candidate_vouchers == [shipment.voucher_no]
        assert run.issued_by_id == 5

    def test_adopted(self, monkeypatch):
        _candidate("7001", printed=True)
        _acs_returns(
            monkeypatch,
            dict(SILENT),
            lists=[{"PickupList_No": "PL9"}],
            vouchers={"PL9": ["7001"]},
        )

        AcsService.issue_daily_pickup_list()

        run = AcsPickupListRun.objects.get()
        assert run.outcome == AcsPickupListRunOutcome.ADOPTED
        assert run.pickup_list.pickup_list_no == "PL9"

    def test_documented_refusal(self, monkeypatch):
        _candidate("7001")
        _acs_returns(
            monkeypatch,
            {
                "PickupList_No": None,
                "Unprinted_Found": 1,
                "Unprinted_Vouchers": ["7001"],
                "Error_Message": "ACS says print",
            },
        )

        with pytest.raises(AcsUnprintedVouchersError):
            AcsService.issue_daily_pickup_list()

        run = AcsPickupListRun.objects.get()
        assert run.outcome == AcsPickupListRunOutcome.BLOCKED_UNPRINTED
        assert run.unprinted_vouchers_acs == ["7001"]
        assert run.unprinted_vouchers_local == ["7001"]
        assert run.acs_message == "ACS says print"

    def test_silent_refusal(self, monkeypatch):
        _candidate("7001")
        _acs_returns(monkeypatch, dict(SILENT))

        with pytest.raises(AcsUnprintedVouchersError):
            AcsService.issue_daily_pickup_list()

        run = AcsPickupListRun.objects.get()
        assert run.outcome == AcsPickupListRunOutcome.BLOCKED_UNPRINTED_SILENT
        assert run.unprinted_vouchers_acs == []
        assert run.unprinted_vouchers_local == ["7001"]

    def test_unexplained_silence_is_an_error_run(self, monkeypatch):
        _candidate("7001", printed=True)
        _acs_returns(monkeypatch, dict(SILENT))

        with pytest.raises(AcsAPIError):
            AcsService.issue_daily_pickup_list()

        run = AcsPickupListRun.objects.get()
        assert run.outcome == AcsPickupListRunOutcome.ERROR
        assert "no PickupList_No" in run.error_message

    def test_nothing_to_issue(self):
        AcsService.issue_daily_pickup_list()

        run = AcsPickupListRun.objects.get()
        assert run.outcome == AcsPickupListRunOutcome.NOTHING_TO_ISSUE
        assert run.candidate_vouchers == []

    def test_a_failing_write_never_masks_the_outcome(self, monkeypatch, caplog):
        _candidate("7001", printed=True)
        _acs_returns(monkeypatch, dict(SILENT))

        with (
            patch.object(
                AcsPickupListRun.objects,
                "create",
                side_effect=RuntimeError("db"),
            ),
            caplog.at_level(logging.WARNING, logger="shipping_acs.services"),
            pytest.raises(AcsAPIError),
        ):
            AcsService.issue_daily_pickup_list()

        assert any(
            "Could not record the ACS pickup list run" in r.getMessage()
            for r in caplog.records
        )


@pytest.fixture
def staff_request():
    request = RequestFactory().get("/")
    request.user = UserAccountFactory(admin=True)
    return request


class TestTimeline:
    def test_orders_label_runs_calls_and_tracking(self, monkeypatch):
        shipment = _candidate("7001")
        AcsShipment.objects.filter(pk=shipment.pk).update(
            label_printed_at=timezone.now()
        )
        shipment.refresh_from_db()
        AcsApiCall.objects.create(
            alias="ACS_Print_Voucher",
            duration_ms=3,
            outcome="ok",
            voucher_no="7001",
        )
        _acs_returns(monkeypatch, dict(SILENT))
        AcsShipment.objects.filter(pk=shipment.pk).update(label_printed_at=None)
        with pytest.raises(AcsUnprintedVouchersError):
            AcsService.issue_daily_pickup_list()
        shipment.refresh_from_db()

        kinds = [entry.kind for entry in shipment_timeline(shipment)]

        assert kinds.count("run") == 1
        assert "call" in kinds
        assert kinds[0] == "shipment"
        timeline = shipment_timeline(shipment)
        assert [e.at for e in timeline] == sorted(e.at for e in timeline)

    def test_shipment_admin_renders_the_timeline(self, staff_request):
        shipment = _candidate("7001")
        admin = AcsShipmentAdmin(AcsShipment, AdminSite())

        html = str(admin.diagnostics_timeline(shipment))

        assert "Shipment row created" in html


class TestAdmins:
    def test_api_call_admin_is_superuser_only_and_read_only(
        self, staff_request
    ):
        admin = AcsApiCallAdmin(AcsApiCall, AdminSite())
        assert admin.has_view_permission(staff_request)
        assert not admin.has_add_permission(staff_request)
        assert not admin.has_change_permission(staff_request)

        staff_request.user = UserAccountFactory(admin=False, is_staff=True)
        assert not admin.has_view_permission(staff_request)
        assert not admin.has_module_permission(staff_request)

    def test_run_admin_is_read_only(self, staff_request):
        admin = AcsPickupListRunAdmin(AcsPickupListRun, AdminSite())
        assert not admin.has_add_permission(staff_request)
        assert not admin.has_change_permission(staff_request)
        assert not admin.has_delete_permission(staff_request)
