"""Enquiries and feedback are deleted after the store's retention period.

``CONTACT_RETENTION_DAYS`` is one per-store setting for both models.
Deleting an enquiry goes through the ORM so its attachment files go with
it (``contact.signals.delete_attachment_file``).
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from django.contrib import admin as django_admin
from django.test import RequestFactory
from django.utils import timezone
from extra_settings.models import Setting

from contact import tasks
from contact.admin import ContactAdmin, FeedbackAdmin
from contact.factories import ContactFactory, FeedbackFactory
from contact.models import Contact, ContactAttachment, Feedback
from contact.tasks import purge_expired_contact_records
from tests.unit.contact.test_attachment_tasks import make_attachment
from user.factories.account import UserAccountFactory

pytestmark = [pytest.mark.django_db, pytest.mark.assert_english]


def _set_days(days: int) -> None:
    Setting.objects.update_or_create(
        name="CONTACT_RETENTION_DAYS",
        defaults={"value_type": Setting.TYPE_INT, "value_int": days},
    )


def _aged(model, factory, days: int):
    row = factory()
    # ``auto_now_add`` ignores what the factory was given.
    model.objects.filter(pk=row.pk).update(
        created_at=timezone.now() - timedelta(days=days)
    )
    return row


class TestPurgeExpiredContactRecords:
    def test_rows_past_the_period_are_deleted_and_recent_ones_kept(self):
        _set_days(30)
        old_contact = _aged(Contact, ContactFactory, 31)
        new_contact = _aged(Contact, ContactFactory, 29)
        old_feedback = _aged(Feedback, FeedbackFactory, 31)
        new_feedback = _aged(Feedback, FeedbackFactory, 29)

        result = purge_expired_contact_records()

        assert result == {"contacts": 1, "feedback": 1}
        assert not Contact.objects.filter(pk=old_contact.pk).exists()
        assert Contact.objects.filter(pk=new_contact.pk).exists()
        assert not Feedback.objects.filter(pk=old_feedback.pk).exists()
        assert Feedback.objects.filter(pk=new_feedback.pk).exists()

    def test_the_default_period_is_a_year(self):
        Setting.objects.filter(name="CONTACT_RETENTION_DAYS").delete()
        old = _aged(Contact, ContactFactory, 366)
        recent = _aged(Contact, ContactFactory, 364)

        purge_expired_contact_records()

        assert not Contact.objects.filter(pk=old.pk).exists()
        assert Contact.objects.filter(pk=recent.pk).exists()

    def test_rows_far_older_than_the_period_go_on_the_first_run(self):
        """A store switching the sweep on carries years of backlog."""
        _set_days(365)
        for _ in range(3):
            _aged(Contact, ContactFactory, 2000)
            _aged(Feedback, FeedbackFactory, 2000)

        assert purge_expired_contact_records() == {
            "contacts": 3,
            "feedback": 3,
        }
        assert Contact.objects.count() == 0
        assert Feedback.objects.count() == 0

    def test_zero_keeps_everything(self):
        _set_days(0)
        _aged(Contact, ContactFactory, 4000)
        _aged(Feedback, FeedbackFactory, 4000)

        assert purge_expired_contact_records() == {
            "contacts": 0,
            "feedback": 0,
        }
        assert Contact.objects.count() == 1
        assert Feedback.objects.count() == 1

    def test_deleting_an_enquiry_removes_its_attachment_files(
        self, private_tree
    ):
        _set_days(30)
        contact = _aged(Contact, ContactFactory, 90)
        make_attachment(contact=contact)
        assert list(private_tree.rglob("*.pdf"))

        purge_expired_contact_records()

        assert ContactAttachment.objects.count() == 0
        assert list(private_tree.rglob("*.pdf")) == []

    def test_a_backlog_larger_than_one_batch_is_fully_deleted(self):
        _set_days(30)
        for _ in range(5):
            _aged(Contact, ContactFactory, 90)

        with mock.patch.object(tasks, "RETENTION_BATCH_SIZE", 2):
            assert purge_expired_contact_records()["contacts"] == 5

        assert Contact.objects.count() == 0


class TestRetentionNoticeInTheAdmin:
    @pytest.mark.parametrize(
        ("model", "admin_class"),
        [(Contact, ContactAdmin), (Feedback, FeedbackAdmin)],
    )
    @pytest.mark.parametrize(
        ("days", "expected"),
        [
            (365, "older than 365 days are deleted automatically"),
            (1, "older than 1 day are deleted automatically"),
            (0, "kept indefinitely"),
        ],
    )
    def test_the_list_states_the_period(
        self, model, admin_class, days, expected
    ):
        _set_days(days)
        model_admin = admin_class(model, django_admin.site)
        request = RequestFactory().get("/admin/contact/")
        request.user = UserAccountFactory(is_staff=True, is_superuser=True)

        with mock.patch(
            "unfold.admin.ModelAdmin.changelist_view"
        ) as changelist_view:
            model_admin.changelist_view(request)

        context = changelist_view.call_args.args[1]
        assert expected in context["retention_notice"]
        assert "CONTACT_RETENTION_DAYS" in context["retention_notice"]
