"""The console reads the schedule, switches an entry, runs one. Nothing else."""

from unittest import mock

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from admin.platform_site import platform_admin_site
from scheduler.admin import PeriodicTaskAdmin
from scheduler.models import PeriodicTask
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

DECLARED = "monitor-system-health"
CHANGELIST = "admin:scheduler_periodictask_changelist"


@pytest.fixture
def client():
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )
    return client


def _act(client, action, *rows):
    return client.post(
        reverse(CHANGELIST),
        {"action": action, "_selected_action": [row.pk for row in rows]},
    )


def _change_url(row):
    return reverse("admin:scheduler_periodictask_change", args=[row.pk])


def test_it_is_registered_on_the_platform_console():
    assert isinstance(
        platform_admin_site._registry[PeriodicTask], PeriodicTaskAdmin
    )


def test_the_list_shows_what_the_settings_declare(client, settings):
    PeriodicTask.objects.create(name=DECLARED)

    content = client.get(reverse(CHANGELIST)).content.decode()

    assert settings.CELERY_BEAT_SCHEDULE[DECLARED]["task"] in content
    assert "crontab" not in content


def test_the_entry_the_scheduler_installs_itself_renders(client):
    row = PeriodicTask.objects.create(name="celery.backend_cleanup")

    assert client.get(reverse(CHANGELIST)).status_code == 200
    assert client.get(_change_url(row)).status_code == 200


def test_nothing_can_be_edited_added_or_deleted(client):
    row = PeriodicTask.objects.create(name=DECLARED)

    assert client.get(_change_url(row)).status_code == 200
    assert client.post(_change_url(row), {"name": "renamed"}).status_code == 403
    assert (
        client.get(reverse("admin:scheduler_periodictask_add")).status_code
        == 403
    )
    assert (
        client.post(
            reverse("admin:scheduler_periodictask_delete", args=[row.pk]),
            {"post": "yes"},
        ).status_code
        == 403
    )
    row.refresh_from_db()
    assert row.name == DECLARED


def test_switching_off_and_on(client):
    row = PeriodicTask.objects.create(name=DECLARED)

    assert _act(client, "switch_off", row).status_code == 302
    row.refresh_from_db()
    assert row.enabled is False
    assert row.resumed_at is None

    assert _act(client, "switch_on", row).status_code == 302
    row.refresh_from_db()
    assert row.enabled is True
    assert row.resumed_at is not None


def test_switching_on_what_is_already_on_keeps_its_stamp():
    PeriodicTask.objects.create(name=DECLARED)

    assert PeriodicTask.objects.switch_on() == 0
    assert PeriodicTask.objects.get(name=DECLARED).resumed_at is None


def test_a_switch_is_recorded_against_who_did_it(client):
    row = PeriodicTask.objects.create(name=DECLARED)

    _act(client, "switch_off", row)

    assert LogEntry.objects.filter(
        object_id=str(row.pk), change_message="Switched off"
    ).exists()


def test_running_by_hand_queues_it_in_public_without_recording_a_run(
    client, settings
):
    row = PeriodicTask.objects.create(name=DECLARED)

    with mock.patch("scheduler.admin.celery_app.send_task") as send_task:
        response = _act(client, "run_now", row)

    assert response.status_code == 302
    args, kwargs = send_task.call_args
    assert args == (settings.CELERY_BEAT_SCHEDULE[DECLARED]["task"],)
    assert kwargs["headers"] == {
        "periodic_task_name": DECLARED,
        "_schema_name": "public",
    }
    assert kwargs["ignore_result"] is True
    row.refresh_from_db()
    assert row.last_run_at is None


def test_an_entry_with_nothing_declared_is_refused_when_run_by_hand(client):
    row = PeriodicTask.objects.create(name="celery.backend_cleanup")

    with mock.patch("scheduler.admin.celery_app.send_task") as send_task:
        response = _act(client, "run_now", row)

    assert response.status_code == 302
    send_task.assert_not_called()


def test_a_viewer_can_read_but_not_operate():
    viewer = UserAccountFactory(is_staff=True)
    viewer.user_permissions.add(
        Permission.objects.get(
            codename="view_periodictask", content_type__app_label="scheduler"
        )
    )
    row = PeriodicTask.objects.create(name=DECLARED)
    client = Client()
    client.force_login(
        viewer, backend="tenant.auth_backends.PlatformStaffBackend"
    )

    _act(client, "switch_off", row)

    row.refresh_from_db()
    assert row.enabled is True
