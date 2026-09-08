"""Scanning, and the two sweeps that keep the private tree bounded.

The invariants worth protecting here:

* A verdict that never arrived is not a clean one.
* INFECTED keeps the ROW and drops the BYTES.
* An abandoned upload does not outlive its claim window.
* Retention drops the bytes and keeps the record.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from unittest import mock

import pytest
from celery.exceptions import Retry
from django.core.files.base import ContentFile
from django.utils import timezone

from contact.factories import ContactFactory
from contact.models import ContactAttachment
from contact.scanners import (
    CLEAN,
    ERROR,
    INFECTED,
    SKIPPED,
    AttachmentScannerError,
    AttachmentScannerInterface,
)
from contact.tasks import (
    purge_expired_attachment_files,
    reap_unclaimed_attachments,
    scan_contact_attachment,
)
from tests.unit.contact.conftest import pdf_bytes

pytestmark = pytest.mark.django_db


def make_attachment(
    *,
    contact=None,
    payload: bytes | None = None,
    claim_in: timedelta = timedelta(hours=6),
    created_at=None,
    original_name: str = "plan.pdf",
    **extra,
):
    payload = pdf_bytes(64) if payload is None else payload
    attachment = ContactAttachment(
        contact=contact,
        original_name=original_name,
        content_type="application/pdf",
        size=len(payload),
        checksum=hashlib.sha256(payload).hexdigest(),
        claim_deadline=timezone.now() + claim_in,
        **extra,
    )
    attachment.file.save("plan.pdf", ContentFile(payload), save=False)
    attachment.save()
    if created_at is not None:
        # ``auto_now_add`` ignores what the constructor was given.
        ContactAttachment.objects.filter(pk=attachment.pk).update(
            created_at=created_at
        )
        attachment.refresh_from_db()
    return attachment


class _Verdict(AttachmentScannerInterface):
    code = "test-verdict"

    def __init__(self, verdict, detail="because"):
        self.verdict = verdict
        self.detail = detail
        self.seen = b""

    def scan(self, stream, *, size):
        self.seen = stream.read()
        return self.verdict, self.detail


class _Broken(AttachmentScannerInterface):
    code = "test-broken"

    def __init__(self):
        self.calls = 0

    def scan(self, stream, *, size):
        self.calls += 1
        raise AttachmentScannerError("daemon went away")


def _with_scanner(scanner):
    return mock.patch("contact.scanners.get_scanner", return_value=scanner)


class TestScanContactAttachment:
    def test_a_clean_verdict_is_recorded_and_downloadable(self, private_tree):
        attachment = make_attachment()
        scanner = _Verdict(CLEAN, "stream: OK")

        with _with_scanner(scanner):
            assert scan_contact_attachment(attachment.pk) == CLEAN

        attachment.refresh_from_db()
        assert attachment.scan_status == ContactAttachment.ScanStatus.CLEAN
        assert attachment.scan_detail == "stream: OK"
        assert attachment.scanned_at is not None
        assert attachment.is_downloadable() is True
        # The engine saw the stored bytes, not a claim about them.
        assert scanner.seen == pdf_bytes(64)

    def test_the_null_default_records_skipped(self, private_tree):
        """No engine configured: honest, and still downloadable."""
        attachment = make_attachment()
        assert scan_contact_attachment(attachment.pk) == SKIPPED
        attachment.refresh_from_db()
        assert attachment.scan_status == ContactAttachment.ScanStatus.SKIPPED
        assert "No scanner configured" in attachment.scan_detail
        assert attachment.is_downloadable() is True

    def test_infected_keeps_the_row_and_drops_the_bytes(self, private_tree):
        attachment = make_attachment()
        stored = private_tree.rglob("*.pdf")
        assert any(stored)

        with _with_scanner(_Verdict(INFECTED, "Eicar FOUND")):
            assert scan_contact_attachment(attachment.pk) == INFECTED

        attachment.refresh_from_db()
        assert attachment.scan_status == ContactAttachment.ScanStatus.INFECTED
        assert attachment.scan_detail == "Eicar FOUND"
        assert not attachment.file
        # The record of what arrived survives.
        assert attachment.original_name == "plan.pdf"
        assert attachment.size == len(pdf_bytes(64))
        assert attachment.is_downloadable() is False
        assert list(private_tree.rglob("*.pdf")) == []

    def test_a_transport_failure_asks_to_be_retried(self, private_tree):
        """A daemon that went away is worth trying again, not a verdict."""
        attachment = make_attachment()

        with _with_scanner(_Broken()):
            with pytest.raises(Retry):
                scan_contact_attachment.apply(args=[attachment.pk]).get()

        attachment.refresh_from_db()
        # Still PENDING: nothing has said anything about this file.
        assert attachment.scan_status == ContactAttachment.ScanStatus.PENDING
        assert attachment.is_downloadable() is False

    def test_the_last_attempt_records_error_instead_of_failing(
        self, private_tree
    ):
        """The terminal state must be a VERDICT ROW, not a dead task.

        ``Task.retry(exc=...)`` re-raises the original exception once
        the limit is passed, so a task that simply let the error
        through would leave the row on PENDING for good. Simulated by
        entering with the retry counter already spent, because eager
        execution raises ``Retry`` rather than looping.
        """
        attachment = make_attachment()
        spent = scan_contact_attachment.max_retries

        with _with_scanner(_Broken()):
            result = scan_contact_attachment.apply(
                args=[attachment.pk], retries=spent
            )

        assert result.get() == ERROR
        attachment.refresh_from_db()
        assert attachment.scan_status == ContactAttachment.ScanStatus.ERROR
        assert "daemon went away" in attachment.scan_detail
        assert attachment.is_downloadable() is False
        # The bytes are KEPT: nothing said they were bad.
        assert attachment.file

    def test_a_row_reaped_before_the_scan_is_not_an_error(self, private_tree):
        assert scan_contact_attachment(999_999) == "missing"

    def test_a_row_whose_bytes_are_gone_is_not_an_error(self, private_tree):
        attachment = make_attachment()
        attachment.file.delete(save=True)
        assert scan_contact_attachment(attachment.pk) == "missing"

    def test_the_detail_is_truncated_to_the_column(self, private_tree):
        attachment = make_attachment()
        with _with_scanner(_Verdict(CLEAN, "x" * 900)):
            scan_contact_attachment(attachment.pk)
        attachment.refresh_from_db()
        assert len(attachment.scan_detail) == 255


class TestReapUnclaimedAttachments:
    def test_an_abandoned_upload_goes_with_its_bytes(self, private_tree):
        make_attachment(claim_in=timedelta(hours=-1))

        assert reap_unclaimed_attachments() == 1

        assert ContactAttachment.objects.count() == 0
        assert list(private_tree.rglob("*.pdf")) == []

    def test_an_upload_still_inside_its_window_is_left_alone(
        self, private_tree
    ):
        make_attachment(claim_in=timedelta(hours=1))
        assert reap_unclaimed_attachments() == 0
        assert ContactAttachment.objects.count() == 1

    def test_a_claimed_upload_is_never_reaped(self, private_tree):
        """Even long past the deadline: it belongs to an enquiry now."""
        make_attachment(contact=ContactFactory(), claim_in=timedelta(days=-30))
        assert reap_unclaimed_attachments() == 0
        assert ContactAttachment.objects.count() == 1

    def test_deleting_a_row_anywhere_drops_its_bytes(self, private_tree):
        """The invariant the reaper leans on, asserted on its own."""
        attachment = make_attachment()
        attachment.delete()
        assert list(private_tree.rglob("*.pdf")) == []

    def test_deleting_the_enquiry_cascades_to_the_bytes(self, private_tree):
        contact = ContactFactory()
        make_attachment(contact=contact)
        contact.delete()
        assert ContactAttachment.objects.count() == 0
        assert list(private_tree.rglob("*.pdf")) == []


class TestPurgeExpiredAttachmentFiles:
    def _aged(self, days: int):
        return make_attachment(
            contact=ContactFactory(),
            created_at=timezone.now() - timedelta(days=days),
        )

    def test_bytes_go_and_the_record_stays(self, private_tree, settings):
        from extra_settings.models import Setting

        Setting.objects.filter(
            name="CONTACT_ATTACHMENTS_RETENTION_DAYS"
        ).update(value_int=30)
        attachment = self._aged(days=45)

        assert purge_expired_attachment_files() == 1

        attachment.refresh_from_db()
        assert not attachment.file
        assert attachment.original_name == "plan.pdf"
        assert attachment.size == len(pdf_bytes(64))
        assert attachment.checksum
        assert list(private_tree.rglob("*.pdf")) == []

    def test_a_file_inside_the_window_is_kept(self, private_tree):
        from extra_settings.models import Setting

        Setting.objects.filter(
            name="CONTACT_ATTACHMENTS_RETENTION_DAYS"
        ).update(value_int=90)
        attachment = self._aged(days=10)

        assert purge_expired_attachment_files() == 0

        attachment.refresh_from_db()
        assert attachment.file

    def test_zero_days_keeps_files_indefinitely(self, private_tree):
        from extra_settings.models import Setting

        Setting.objects.filter(
            name="CONTACT_ATTACHMENTS_RETENTION_DAYS"
        ).update(value_int=0)
        self._aged(days=4000)
        assert purge_expired_attachment_files() == 0

    def test_an_unclaimed_upload_is_the_reapers_job_not_this_one(
        self, private_tree
    ):
        from extra_settings.models import Setting

        Setting.objects.filter(
            name="CONTACT_ATTACHMENTS_RETENTION_DAYS"
        ).update(value_int=1)
        make_attachment(created_at=timezone.now() - timedelta(days=90))
        assert purge_expired_attachment_files() == 0

    def test_a_row_already_purged_is_not_purged_twice(self, private_tree):
        from extra_settings.models import Setting

        Setting.objects.filter(
            name="CONTACT_ATTACHMENTS_RETENTION_DAYS"
        ).update(value_int=1)
        self._aged(days=90)
        assert purge_expired_attachment_files() == 1
        assert purge_expired_attachment_files() == 0


class TestTheNotificationEmail:
    """Files are never mailed; staff are told they exist."""

    def test_the_attachments_are_listed_with_their_scan_state(
        self, private_tree
    ):
        from django.core import mail

        from contact.tasks import send_contact_notification_email_task

        contact = ContactFactory()
        make_attachment(
            contact=contact,
            original_name="Σχέδιο Α1.pdf",
            scan_status=ContactAttachment.ScanStatus.SKIPPED,
        )
        make_attachment(
            contact=contact,
            original_name="specs.pdf",
            scan_status=ContactAttachment.ScanStatus.INFECTED,
        )
        mail.outbox.clear()

        assert send_contact_notification_email_task(contact.pk) is True

        body = mail.outbox[-1].body
        assert "Attachments (2)" in body
        assert "download in the admin" in body
        assert "Σχέδιο Α1.pdf" in body
        assert "Not scanned" in body
        assert "Infected" in body
        # The bytes themselves never leave the private tree.
        assert mail.outbox[-1].attachments == []

    def test_an_enquiry_with_no_files_says_nothing_about_them(
        self, private_tree
    ):
        from django.core import mail

        from contact.tasks import send_contact_notification_email_task

        contact = ContactFactory()
        mail.outbox.clear()

        send_contact_notification_email_task(contact.pk)

        assert "Attachments" not in mail.outbox[-1].body
