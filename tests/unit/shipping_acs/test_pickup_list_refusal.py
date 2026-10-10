"""ACS pickup-list refusals over unprinted labels.

ACS rejects the WHOLE pickup list while any voucher on it is unprinted,
and ``ACS_Issue_Pickup_List`` takes a pickup date with no voucher list.
On webside that is the normal state of the merchant's day: labels are
printed the next morning, and ACS collects a printed parcel the same
day whether or not a manifest exists (2026-09-21 to 10-10, 43 of 45
parcels collected with no pickup list). So a refusal is recorded as a
``blocked_unprinted`` status and a note on each order it holds back,
and nobody is emailed. Every other refusal still fails the task.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest
from django.apps import apps as django_apps

from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.exceptions import AcsAPIError, AcsUnprintedVouchersError
from shipping_acs.factories import AcsShipmentFactory
from shipping_acs.tasks import issue_daily_acs_pickup_list

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _candidate(voucher_no, *, printed=False):
    """A NEW shipment with a voucher and no pickup list."""
    from django.utils import timezone

    return AcsShipmentFactory(
        voucher_no=voucher_no,
        shipment_state=AcsShipmentState.NEW,
        label_printed_at=timezone.now() if printed else None,
    )


def _refusal(vouchers):
    return AcsUnprintedVouchersError(
        alias="ACS_Issue_Pickup_List",
        error_message="Αδύνατη η έκδοση λίστας παραλαβής.",
        raw={
            "PickupList_No": None,
            "Unprinted_Found": len(vouchers),
            "Unprinted_Vouchers": vouchers,
        },
    )


def _pickup_list_notes(order_id):
    from order.models.history import OrderHistory

    return [
        h.new_value["note"]
        for h in OrderHistory.objects.filter(
            order_id=order_id, change_type="NOTE"
        )
        if "pickup list" in h.new_value["note"]
    ]


class TestPickupListRefusal:
    def test_reports_blocked_without_emailing(
        self, acs_configured_tenant, mailoutbox
    ):
        blocking = _candidate("9800000001")
        # Creating the order sends its own confirmation; only what the
        # task sends counts here.
        mailoutbox.clear()

        with patch(
            "shipping_acs.services.AcsService.issue_daily_pickup_list",
            side_effect=_refusal(["9800000001"]),
        ):
            result = issue_daily_acs_pickup_list.run()

        assert result == {
            "status": "blocked_unprinted",
            "unprinted": ["9800000001"],
            "order_ids": [blocking.order_id],
            "acs_message": "Αδύνατη η έκδοση λίστας παραλαβής.",
        }
        assert [m.subject for m in mailoutbox] == []

    def test_notes_the_block_on_each_order(self, acs_configured_tenant):
        blocking = _candidate("9800000001")

        with patch(
            "shipping_acs.services.AcsService.issue_daily_pickup_list",
            side_effect=_refusal(["9800000001"]),
        ):
            issue_daily_acs_pickup_list.run()

        [note] = _pickup_list_notes(blocking.order_id)
        assert "9800000001" in note
        assert "Αδύνατη η έκδοση" in note
        assert "Issue ACS pickup list now" in note

    def test_acs_voucher_list_wins_over_the_local_flag(
        self, acs_configured_tenant
    ):
        # ACS is authoritative on what "printed" means — a label printed
        # from its own portal never reaches label_printed_at. When ACS
        # names the offenders, those orders get the note, not our guess.
        acs_says = _candidate("9800000001", printed=True)
        local_guess = _candidate("9800000002")

        with patch(
            "shipping_acs.services.AcsService.issue_daily_pickup_list",
            side_effect=_refusal(["9800000001"]),
        ):
            result = issue_daily_acs_pickup_list.run()

        assert result["order_ids"] == [acs_says.order_id]
        assert _pickup_list_notes(acs_says.order_id)
        assert _pickup_list_notes(local_guess.order_id) == []

    def test_a_successful_issue_sends_nothing(
        self, acs_configured_tenant, mailoutbox
    ):
        from shipping_acs.factories import AcsPickupListFactory

        pickup_list = AcsPickupListFactory()
        with patch(
            "shipping_acs.services.AcsService.issue_daily_pickup_list",
            return_value=pickup_list,
        ):
            result = issue_daily_acs_pickup_list.run()

        assert result["status"] == "ok"
        assert [m.subject for m in mailoutbox] == []


class TestRefusalWithNothingUnprinted:
    """ACS can refuse while naming no unprinted voucher at all.

    Production, 2026-09-10 and 09-11: ``PickupList_No: null`` with
    ``Unprinted_Found: 0`` and an empty ``Unprinted_Vouchers``. The
    service returns ``None`` for an empty day (see ``test_service.py``);
    a dead voucher is ``check_stale_acs_shipments``' to report.
    """

    def test_an_empty_day_neither_fails_nor_emails(
        self, acs_configured_tenant, mailoutbox
    ):
        with patch(
            "shipping_acs.services.AcsService.issue_daily_pickup_list",
            return_value=None,
        ):
            result = issue_daily_acs_pickup_list.run()

        assert result["status"] == "noop"
        assert [m.subject for m in mailoutbox] == []

    def test_a_refusal_with_nothing_unprinted_raises_without_emailing(
        self, acs_configured_tenant, mailoutbox
    ):
        """A live parcel IS waiting and ACS still refused, naming nothing
        unprinted. Never observed; it stays a hard failure carried by the
        service's ERROR log and the failed task, not an email.
        """
        _candidate("9803334192", printed=True)
        mailoutbox.clear()

        with (
            patch(
                "shipping_acs.services.AcsService.issue_daily_pickup_list",
                side_effect=AcsAPIError(
                    alias="ACS_Issue_Pickup_List",
                    error_message="",
                    raw={
                        "PickupList_No": None,
                        "Unprinted_Found": 0,
                        "Unprinted_Vouchers": [],
                    },
                ),
            ),
            pytest.raises(AcsAPIError),
        ):
            issue_daily_acs_pickup_list.run()

        assert [m.subject for m in mailoutbox] == []


class TestRetiredWarningBeatRow:
    """The 15:45 warning task is gone, and so must be its beat row.

    ``DatabaseScheduler`` never deletes a row whose entry left
    ``CELERY_BEAT_SCHEDULE``; without the migration it would keep firing
    a task that no longer exists.
    """

    migration = importlib.import_module(
        "tenant.migrations.0049_remove_warn_unprinted_acs_vouchers_beat"
    )

    def test_no_schedule_entry_names_the_retired_task(self, settings):
        assert self.migration.TASK_NAME not in settings.CELERY_BEAT_SCHEDULE
        assert not any(
            "warn_unprinted" in entry["task"]
            for entry in settings.CELERY_BEAT_SCHEDULE.values()
        )

    def test_migration_deletes_the_row_and_flags_the_change(self):
        from django.utils import timezone
        from django_celery_beat.models import (
            CrontabSchedule,
            PeriodicTask,
            PeriodicTasks,
        )

        crontab = CrontabSchedule.objects.create(
            minute="45", hour="15", day_of_week="mon-fri"
        )
        PeriodicTask.objects.create(
            name=self.migration.TASK_NAME,
            task="tenant.tasks.fanout_warn_unprinted_acs_vouchers",
            crontab=crontab,
        )
        kept = PeriodicTask.objects.create(
            name="issue-acs-pickup-list",
            task="tenant.tasks.fanout_issue_daily_acs_pickup_list",
            crontab=crontab,
        )
        before = timezone.now()

        self.migration.delete_beat_row(django_apps, None)

        names = set(PeriodicTask.objects.values_list("name", flat=True))
        assert self.migration.TASK_NAME not in names
        assert kept.name in names
        assert PeriodicTasks.objects.get(ident=1).last_update >= before
