"""``CELERY_BEAT_SCHEDULE`` is the whole schedule.

``DatabaseScheduler`` never removes a row whose entry is gone. A local
database kept firing ``update-order-statuses-from-shipping`` for six months
after its task was deleted, filling the queue with messages no worker knew.
"""

from __future__ import annotations

import pytest
from celery.signals import beat_init
from django.test import Client
from django.urls import reverse
from django_celery_beat.models import (
    CrontabSchedule,
    IntervalSchedule,
    PeriodicTask,
    PeriodicTasks,
)

from core.beat import BACKEND_CLEANUP_ENTRY, delete_undeclared_periodic_tasks
from core.celery import celery_app
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

DECLARED = "monitor-system-health"
REMOVED = "update-order-statuses-from-shipping"


def _row(name: str, task: str = "core.tasks.monitor_system_health"):
    crontab, _ = CrontabSchedule.objects.get_or_create(
        minute="0", hour="*", timezone="Europe/Athens"
    )
    return PeriodicTask.objects.create(name=name, task=task, crontab=crontab)


def test_a_row_the_schedule_does_not_declare_is_deleted():
    _row(REMOVED, task="order.tasks.update_order_statuses_from_shipping")
    _row(DECLARED)

    assert delete_undeclared_periodic_tasks() == 1

    assert not PeriodicTask.objects.filter(name=REMOVED).exists()
    assert PeriodicTask.objects.filter(name=DECLARED).exists()


def test_the_cleanup_entry_the_scheduler_installs_itself_stays():
    _row(BACKEND_CLEANUP_ENTRY, task="celery.backend_cleanup")

    assert delete_undeclared_periodic_tasks() == 0


def test_a_declared_row_that_is_switched_off_stays_off():
    row = _row(DECLARED)
    row.enabled = False
    row.save()

    delete_undeclared_periodic_tasks()

    row.refresh_from_db()
    assert row.enabled is False


def test_deleting_tells_a_running_scheduler_to_reload():
    _row(REMOVED)
    PeriodicTasks.objects.all().delete()

    delete_undeclared_periodic_tasks()

    assert PeriodicTasks.last_change() is not None


def test_beat_start_runs_it():
    _row(REMOVED)

    beat_init.send(sender=celery_app)

    assert not PeriodicTask.objects.filter(name=REMOVED).exists()


@pytest.fixture
def client():
    client = Client()
    client.force_login(
        UserAccountFactory(admin=True),
        backend="tenant.auth_backends.PlatformStaffBackend",
    )
    return client


def _change_url(row) -> str:
    return reverse(
        "admin:django_celery_beat_periodictask_change", args=[row.pk]
    )


class TestPeriodicTaskAdmin:
    def test_the_change_page_renders_the_schedule(self, client):
        row = _row(DECLARED)

        response = client.get(_change_url(row))

        assert response.status_code == 200
        assert DECLARED in response.content.decode()

    def test_a_post_changes_enabled_and_nothing_else(self, client):
        row = _row(DECLARED)
        other = IntervalSchedule.objects.create(
            every=1, period=IntervalSchedule.DAYS
        )

        response = client.post(
            _change_url(row),
            {
                "name": "renamed",
                "task": "core.tasks.other",
                "interval": other.pk,
                "kwargs": '{"a": 1}',
            },
        )

        assert response.status_code == 302
        row.refresh_from_db()
        assert row.enabled is False
        assert row.name == DECLARED
        assert row.task == "core.tasks.monitor_system_health"
        assert row.interval_id is None
        assert row.kwargs == "{}"

    def test_a_row_cannot_be_added_or_deleted(self, client):
        row = _row(DECLARED)

        assert (
            client.get(
                reverse("admin:django_celery_beat_periodictask_add")
            ).status_code
            == 403
        )
        assert (
            client.post(
                reverse(
                    "admin:django_celery_beat_periodictask_delete",
                    args=[row.pk],
                ),
                {"post": "yes"},
            ).status_code
            == 403
        )
        assert PeriodicTask.objects.filter(pk=row.pk).exists()


@pytest.mark.parametrize(
    "model_name",
    [
        "crontabschedule",
        "intervalschedule",
        "solarschedule",
        "clockedschedule",
    ],
)
def test_a_schedule_row_can_only_be_read(client, model_name):
    url = reverse(f"admin:django_celery_beat_{model_name}_add")
    changelist = reverse(f"admin:django_celery_beat_{model_name}_changelist")

    assert client.get(url).status_code == 403
    assert client.get(changelist).status_code == 200


def test_a_crontab_row_cannot_be_edited(client):
    crontab = CrontabSchedule.objects.create(minute="7", hour="*")
    url = reverse(
        "admin:django_celery_beat_crontabschedule_change", args=[crontab.pk]
    )

    assert client.get(url).status_code == 200
    assert client.post(url, {"minute": "9"}).status_code == 403
    crontab.refresh_from_db()
    assert crontab.minute == "7"
