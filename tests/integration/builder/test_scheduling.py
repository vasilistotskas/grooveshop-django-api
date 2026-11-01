"""Scheduled publishing: frozen at scheduling, released when due, never twice."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from builder import scheduling, services
from builder.errors import BuilderError, DocumentInvalid, NotFoundError
from builder.factories import BuilderScheduledPublishFactory
from builder.models import (
    BuilderDocument,
    BuilderRelease,
    BuilderScheduledPublish,
    BuilderVersion,
    ScheduleStatus,
)
from builder.tasks import run_due_publishes
from product.factories.product import ProductFactory
from tenant.models import Tenant
from tests.integration.builder.conftest import api_as
from tests.utils.builder import document, hero

pytestmark = pytest.mark.django_db

HOME = "template:home"


def soon(**delta) -> datetime:
    return timezone.now() + timedelta(**(delta or {"hours": 1}))


def save(admin, title="Welcome", revision=0, **settings_):
    return services.save_draft(
        admin,
        key=HOME,
        draft=document(("a", hero(title, **settings_))),
        i18n={},
        expected_revision=revision,
    )


def due(schedule: BuilderScheduledPublish) -> None:
    """Move its time into the past, as waiting would."""
    BuilderScheduledPublish.objects.filter(pk=schedule.pk).update(
        scheduled_for=timezone.now() - timedelta(minutes=1)
    )


def issue_codes(error: DocumentInvalid) -> set[str]:
    return {issue.code for issue in error.issues}


@pytest.fixture(autouse=True)
def _ready(catalogue, features_on):
    return None


class TestScheduling:
    def test_it_freezes_what_was_checked_and_releases_nothing(
        self, admin, tenant
    ):
        save(admin)
        before = Tenant.objects.get(pk=tenant.pk).cache_generation

        schedule = scheduling.schedule_publish(
            admin, keys=[HOME], when=soon(), note="launch"
        )

        assert schedule.status == ScheduleStatus.PENDING
        assert schedule.note == "launch"
        assert schedule.created_by_staff_id == admin.pk
        assert schedule.revisions == {HOME: 1}
        assert not BuilderRelease.objects.exists()
        assert Tenant.objects.get(pk=tenant.pk).cache_generation == before

    @pytest.mark.parametrize(
        ("when", "code"),
        [
            (
                lambda: timezone.now() - timedelta(minutes=1),
                "schedule_in_the_past",
            ),
            (lambda: timezone.now() + timedelta(days=4000), "schedule_too_far"),
            (lambda: datetime(2099, 1, 1, 9, 0), "invalid_schedule_time"),
        ],
    )
    def test_the_time_must_be_a_future_aware_time_within_the_horizon(
        self, admin, when, code
    ):
        save(admin)

        with pytest.raises(DocumentInvalid) as caught:
            scheduling.schedule_publish(admin, keys=[HOME], when=when())

        assert issue_codes(caught.value) == {code}
        assert not BuilderScheduledPublish.objects.exists()

    def test_it_is_checked_like_a_publish(self, admin):
        save(admin, productId=987654)

        with pytest.raises(DocumentInvalid) as caught:
            scheduling.schedule_publish(admin, keys=[HOME], when=soon())

        assert issue_codes(caught.value) == {"reference_not_found"}

    def test_something_must_change_and_something_must_be_named(self, admin):
        save(admin)
        services.publish(admin, keys=[HOME])

        with pytest.raises(DocumentInvalid) as same:
            scheduling.schedule_publish(admin, keys=[HOME], when=soon())
        with pytest.raises(DocumentInvalid) as none:
            scheduling.schedule_publish(admin, keys=[], when=soon())
        with pytest.raises(NotFoundError):
            scheduling.schedule_publish(
                admin, keys=["template:missing"], when=soon()
            )

        assert issue_codes(same.value) == {"nothing_to_publish"}
        assert issue_codes(none.value) == {"nothing_to_publish"}

    def test_the_number_waiting_is_limited(self, admin, settings):
        settings.BUILDER_MAX_PENDING_SCHEDULES = 1
        save(admin)
        scheduling.schedule_publish(admin, keys=[HOME], when=soon())

        with pytest.raises(DocumentInvalid) as caught:
            scheduling.schedule_publish(admin, keys=[HOME], when=soon())

        assert issue_codes(caught.value) == {"too_many_schedules"}

    def test_staff_cannot_schedule(self, admin, staff):
        save(admin)

        with pytest.raises(PermissionDenied):
            scheduling.schedule_publish(staff, keys=[HOME], when=soon())

    def test_frozen_versions_are_not_pruned_while_pending(
        self, admin, settings
    ):
        settings.BUILDER_UNRELEASED_CHECKPOINTS_KEPT = 0
        save(admin)
        schedule = scheduling.schedule_publish(admin, keys=[HOME], when=soon())
        revision = 1
        for n in range(3):
            revision = save(admin, f"v{n}", revision).draft_revision
            services.create_checkpoint(admin, key=HOME)

        frozen = schedule.versions[HOME]
        assert BuilderVersion.objects.filter(pk=frozen).exists()


class TestExecuting:
    def test_a_due_schedule_becomes_one_release_made_by_who_scheduled_it(
        self, admin, tenant
    ):
        save(admin, "Spring")
        schedule = scheduling.schedule_publish(
            admin, keys=[HOME], when=soon(), note="spring"
        )
        due(schedule)
        before = Tenant.objects.get(pk=tenant.pk).cache_generation

        number = scheduling.execute(schedule.pk)

        schedule.refresh_from_db()
        release = BuilderRelease.objects.get()
        assert number == release.number == 1
        assert (schedule.status, schedule.release, schedule.failure) == (
            ScheduleStatus.PUBLISHED,
            release,
            [],
        )
        assert schedule.executed_at is not None
        assert release.published_by_staff_id == admin.pk
        assert release.note == "spring"
        assert release.versions == schedule.versions
        assert Tenant.objects.get(pk=tenant.pk).cache_generation > before
        assert BuilderDocument.objects.get(key=HOME).published_revision == 1

    def test_what_goes_live_is_what_was_scheduled_not_what_was_edited_since(
        self, admin
    ):
        save(admin, "Scheduled")
        schedule = scheduling.schedule_publish(admin, keys=[HOME], when=soon())
        save(admin, "Edited after", revision=1)
        due(schedule)

        scheduling.execute(schedule.pk)

        live = BuilderVersion.objects.get(
            pk=services.published_versions()[HOME]
        )
        assert live.data["sections"]["a"]["settings"]["title"] == "Scheduled"
        document_ = BuilderDocument.objects.get(key=HOME)
        assert document_.draft["sections"]["a"]["settings"]["title"] == (
            "Edited after"
        )
        assert document_.published_revision != document_.draft_revision

    def test_it_is_not_released_before_its_time(self, admin):
        save(admin)
        schedule = scheduling.schedule_publish(admin, keys=[HOME], when=soon())

        assert scheduling.execute(schedule.pk) is None
        assert scheduling.run_due() == {"due": 0, "published": 0}
        assert not BuilderRelease.objects.exists()

    def test_it_runs_once_however_often_it_is_swept(self, admin):
        save(admin)
        schedule = scheduling.schedule_publish(admin, keys=[HOME], when=soon())
        due(schedule)

        first = scheduling.run_due()
        second = scheduling.run_due()

        assert first == {"due": 1, "published": 1}
        assert second == {"due": 0, "published": 0}
        assert scheduling.execute(schedule.pk) is None
        assert BuilderRelease.objects.count() == 1

    def test_the_sweep_is_a_task_per_store(self, admin):
        save(admin)
        due(scheduling.schedule_publish(admin, keys=[HOME], when=soon()))

        result = run_due_publishes.apply().get()

        assert result == {"due": 1, "published": 1}
        entry = settings.CELERY_BEAT_SCHEDULE["run-due-builder-publishes"]
        assert entry["task"] == "tenant.tasks.fanout_run_due_builder_publishes"

    def test_several_due_schedules_go_in_order_each_on_its_own(self, admin):
        save(admin, "One")
        first = scheduling.schedule_publish(admin, keys=[HOME], when=soon())
        save(admin, "Two", revision=1)
        second = scheduling.schedule_publish(
            admin, keys=[HOME], when=soon(hours=2)
        )
        due(first)
        BuilderScheduledPublish.objects.filter(pk=second.pk).update(
            scheduled_for=timezone.now() - timedelta(seconds=1)
        )

        assert scheduling.run_due() == {"due": 2, "published": 2}

        live = BuilderVersion.objects.get(
            pk=services.published_versions()[HOME]
        )
        assert live.data["sections"]["a"]["settings"]["title"] == "Two"

    def test_a_reference_deleted_meanwhile_fails_it_whole_and_says_why(
        self, admin
    ):
        product = ProductFactory(active=True)
        save(admin, productId=product.pk)
        schedule = scheduling.schedule_publish(admin, keys=[HOME], when=soon())
        product.delete()
        due(schedule)

        assert scheduling.execute(schedule.pk) is None

        schedule.refresh_from_db()
        assert schedule.status == ScheduleStatus.FAILED
        assert [f["code"] for f in schedule.failure] == ["reference_not_found"]
        assert schedule.failure[0]["document"] == HOME
        assert not BuilderRelease.objects.exists()
        assert scheduling.run_due() == {"due": 0, "published": 0}

    def test_a_deleted_page_fails_it(self, admin):
        banner = document(
            ("b", {"type": "banner@1", "settings": {"text": "x"}})
        )
        page = services.save_draft(
            admin, key="page:about", draft=banner, i18n={}, expected_revision=0
        )
        schedule = scheduling.schedule_publish(
            admin, keys=["page:about"], when=soon()
        )
        services.delete_page(
            admin, key="page:about", expected_revision=page.draft_revision
        )
        due(schedule)

        scheduling.execute(schedule.pk)

        schedule.refresh_from_db()
        assert [f["code"] for f in schedule.failure] == ["document_gone"]

    def test_something_published_meanwhile_leaves_nothing_to_do(self, admin):
        save(admin)
        schedule = scheduling.schedule_publish(admin, keys=[HOME], when=soon())
        services.publish(admin, keys=[HOME])
        due(schedule)

        scheduling.execute(schedule.pk)

        schedule.refresh_from_db()
        assert schedule.status == ScheduleStatus.FAILED
        assert [f["code"] for f in schedule.failure] == ["nothing_to_publish"]
        assert BuilderRelease.objects.count() == 1


class TestCancelAndMove:
    def test_a_cancelled_schedule_never_runs(self, admin):
        save(admin)
        schedule = scheduling.schedule_publish(admin, keys=[HOME], when=soon())

        scheduling.cancel(admin, pk=schedule.pk)
        due(schedule)

        schedule.refresh_from_db()
        assert schedule.status == ScheduleStatus.CANCELLED
        assert scheduling.run_due() == {"due": 0, "published": 0}
        assert not BuilderRelease.objects.exists()

    def test_it_can_be_moved_until_it_runs(self, admin):
        save(admin)
        schedule = scheduling.schedule_publish(admin, keys=[HOME], when=soon())
        later = soon(hours=5)

        moved = scheduling.reschedule(admin, pk=schedule.pk, when=later)

        assert moved.scheduled_for == later
        with pytest.raises(DocumentInvalid):
            scheduling.reschedule(
                admin, pk=schedule.pk, when=timezone.now() - timedelta(hours=1)
            )

    def test_one_that_has_run_or_been_cancelled_cannot_be_changed(self, admin):
        save(admin)
        schedule = scheduling.schedule_publish(admin, keys=[HOME], when=soon())
        due(schedule)
        scheduling.execute(schedule.pk)

        with pytest.raises(scheduling.NotPending):
            scheduling.cancel(admin, pk=schedule.pk)
        with pytest.raises(scheduling.NotPending):
            scheduling.reschedule(admin, pk=schedule.pk, when=soon())
        with pytest.raises(NotFoundError):
            scheduling.cancel(admin, pk=999999)

    def test_staff_cannot_cancel_or_move(self, admin, staff):
        save(admin)
        schedule = scheduling.schedule_publish(admin, keys=[HOME], when=soon())

        with pytest.raises(PermissionDenied):
            scheduling.cancel(staff, pk=schedule.pk)
        with pytest.raises(PermissionDenied):
            scheduling.reschedule(staff, pk=schedule.pk, when=soon())


class TestApi:
    @pytest.fixture
    def client(self, admin):
        save(admin)
        return api_as(admin)

    def test_a_time_without_an_offset_is_read_in_the_stores_timezone(
        self, client
    ):
        local = (timezone.localtime() + timedelta(days=10)).replace(
            microsecond=0, tzinfo=None
        )

        response = client.post(
            reverse("builder:schedule-list"),
            {"documents": [HOME], "scheduledFor": local.isoformat()},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        stored = datetime.fromisoformat(body["scheduledFor"])
        assert (
            stored.utcoffset()
            == timezone.localtime(
                timezone.now() + timedelta(days=10)
            ).utcoffset()
        )
        assert stored.replace(tzinfo=None) == local
        assert (body["status"], body["documents"]) == ("pending", [HOME])

    def test_list_move_cancel(self, client):
        when = (timezone.now() + timedelta(days=3)).isoformat()
        created = client.post(
            reverse("builder:schedule-list"),
            {"documents": [HOME], "scheduledFor": when, "note": "n"},
            format="json",
        ).json()
        url = reverse("builder:schedule-detail", kwargs={"pk": created["id"]})
        later = (timezone.now() + timedelta(days=4)).isoformat()

        moved = client.patch(url, {"scheduledFor": later}, format="json")
        cancelled = client.delete(url)
        again = client.delete(url)
        pending = client.get(
            reverse("builder:schedule-list"), {"status": "pending"}
        )
        done = client.get(
            reverse("builder:schedule-list"), {"status": "cancelled"}
        )

        assert moved.json()["scheduledFor"] != created["scheduledFor"]
        assert cancelled.json()["status"] == "cancelled"
        assert again.status_code == status.HTTP_409_CONFLICT
        assert again.json()["code"] == "schedule_not_pending"
        assert pending.json()["results"] == []
        assert [r["id"] for r in done.json()["results"]] == [created["id"]]
        assert client.get(url).json()["status"] == "cancelled"

    def test_problems_are_the_usual_pointers_and_codes(self, client):
        response = client.post(
            reverse("builder:schedule-list"),
            {
                "documents": [HOME],
                "scheduledFor": (
                    timezone.now() - timedelta(hours=1)
                ).isoformat(),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["errors"][0]["code"] == "schedule_in_the_past"

    def test_staff_may_look_but_not_schedule(self, admin, staff):
        save(admin)
        reader = api_as(staff)

        assert (
            reader.get(reverse("builder:schedule-list")).status_code
            == status.HTTP_200_OK
        )
        response = reader.post(
            reverse("builder:schedule-list"),
            {
                "documents": [HOME],
                "scheduledFor": (
                    timezone.now() + timedelta(days=1)
                ).isoformat(),
            },
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db(transaction=True)
def test_two_sweeps_at_once_make_one_release(admin):
    """Real commits on real connections: the second sweep waits on the row
    lock and then finds the schedule spent."""
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connection

    save(admin)
    schedule = scheduling.schedule_publish(admin, keys=[HOME], when=soon())
    due(schedule)

    def sweep() -> int | None:
        try:
            return scheduling.execute(schedule.pk)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result() for f in [pool.submit(sweep), pool.submit(sweep)]]

    assert sorted(r for r in results if r is not None) == [1]
    assert BuilderRelease.objects.count() == 1


class TestFailureReachesAPerson:
    @pytest.fixture
    def failing(self, admin):
        product = ProductFactory(active=True)
        save(admin, productId=product.pk)
        schedule = scheduling.schedule_publish(admin, keys=[HOME], when=soon())
        product.delete()
        due(schedule)
        return schedule

    def test_a_failure_is_mailed_to_the_stores_staff(self, failing, tenant):
        from django.core import mail

        mail.outbox.clear()

        scheduling.run_due()

        [message] = mail.outbox
        assert (
            "scheduled publish failed" in message.subject.lower()
            or message.subject
        )
        body = message.body
        assert "Template: home" in body or "home" in body
        assert "does not exist" in body or "δεν υπάρχει" in body

    def test_a_publish_that_worked_says_nothing(self, admin):
        from django.core import mail

        save(admin)
        due(scheduling.schedule_publish(admin, keys=[HOME], when=soon()))
        mail.outbox.clear()

        scheduling.run_due()

        assert mail.outbox == []

    def test_a_mail_that_cannot_go_out_does_not_undo_the_failure(self, failing):
        from unittest import mock

        with mock.patch(
            "builder.scheduling.send_email", side_effect=RuntimeError("smtp")
        ):
            scheduling.run_due()

        failing.refresh_from_db()
        assert failing.status == ScheduleStatus.FAILED
        assert scheduling.run_due() == {"due": 0, "published": 0}

    def test_the_mail_is_an_email_type_the_store_can_switch(self):
        from emails.registry import registry

        email_type = registry.get("ops.builder_schedule_failed")

        assert email_type.area == "ops"
        assert email_type.audience.name == "STORE_STAFF"


class TestAcknowledging:
    @pytest.fixture
    def failed(self, admin):
        return BuilderScheduledPublishFactory(
            status=ScheduleStatus.FAILED,
            versions={HOME: 1},
            failure=[{"path": "", "code": "document_gone", "message": "x"}],
        )

    def test_acknowledging_records_who_and_when_and_is_repeatable(
        self, admin, failed
    ):
        scheduling.acknowledge(admin, pk=failed.pk)
        first = BuilderScheduledPublish.objects.get(pk=failed.pk)
        scheduling.acknowledge(admin, pk=failed.pk)
        again = BuilderScheduledPublish.objects.get(pk=failed.pk)

        assert first.acknowledged_by_staff_id == admin.pk
        assert first.acknowledged_at == again.acknowledged_at

    def test_only_a_failure_is_acknowledged_by_someone_who_may_publish(
        self, admin, staff, failed
    ):
        pending = BuilderScheduledPublishFactory()

        with pytest.raises(BuilderError) as caught:
            scheduling.acknowledge(admin, pk=pending.pk)
        with pytest.raises(PermissionDenied):
            scheduling.acknowledge(staff, pk=failed.pk)
        with pytest.raises(NotFoundError):
            scheduling.acknowledge(admin, pk=999999)

        assert caught.value.detail["code"] == "schedule_not_failed"

    def test_through_the_api(self, admin, failed):
        response = api_as(admin).post(
            reverse("builder:schedule-acknowledge", kwargs={"pk": failed.pk})
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["acknowledgedAt"] is not None

    def test_the_dashboard_shows_it_until_it_is_acknowledged(
        self, admin, staff, failed
    ):
        from django.test import RequestFactory
        from django.utils import translation

        from admin.dashboard.store import widgets

        def titles(user) -> list[str]:
            request = RequestFactory().get("/admin/")
            request.user = user
            with translation.override("en"):
                context = widgets.StoreAlerts(
                    request=request
                ).get_context_data()
                return [str(a["title"]) for a in context["alerts"]]

        assert "A scheduled publish failed" in titles(admin)
        assert "A scheduled publish failed" not in titles(staff)

        scheduling.acknowledge(admin, pk=failed.pk)

        assert "A scheduled publish failed" not in titles(admin)

    def test_the_admin_list_acknowledges_in_bulk(self, admin, failed, tenant):
        from django.test import Client
        from django.utils import translation

        client = Client()
        client.force_login(
            admin, backend="tenant.auth_backends.PlatformStaffBackend"
        )
        with translation.override("en"):
            url = reverse("admin:builder_builderscheduledpublish_changelist")
        response = client.post(
            url,
            {"action": "acknowledge_failures", "_selected_action": [failed.pk]},
        )

        assert response.status_code == status.HTTP_302_FOUND
        failed.refresh_from_db()
        assert failed.acknowledged_at is not None
