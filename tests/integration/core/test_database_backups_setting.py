"""``DATABASE_BACKUPS_ENABLED`` decides whether the nightly dump is scheduled.

Off must mean no dump, no dump pruning, and no leftover beat row:
``DatabaseScheduler`` never deletes a row whose entry left
``CELERY_BEAT_SCHEDULE``, and a database cloned from production brings the
rows along.
"""

from __future__ import annotations

import pytest
from django.test import override_settings
from django_celery_beat.models import (
    IntervalSchedule,
    PeriodicTask,
    PeriodicTasks,
)

import settings as settings_module
from core.beat import delete_unscheduled_periodic_tasks
from core.celery import celery_app

pytestmark = pytest.mark.django_db

BACKUP_ENTRIES = {
    "scheduled-database-backup": "core.tasks.scheduled_database_backup",
    "cleanup-old-backups": "core.tasks.cleanup_old_backups",
}


@pytest.fixture
def backups_off(monkeypatch):
    monkeypatch.setattr(settings_module, "DATABASE_BACKUPS_ENABLED", False)


def _cloned_rows() -> None:
    hourly = IntervalSchedule.objects.create(
        every=1, period=IntervalSchedule.HOURS
    )
    for name, task in BACKUP_ENTRIES.items():
        PeriodicTask.objects.create(name=name, task=task, interval=hourly)
    PeriodicTask.objects.create(
        name="monitor-system-health",
        task="core.tasks.monitor_system_health",
        interval=hourly,
    )


def test_on_schedules_both_entries():
    schedule = settings_module.get_celery_beat_schedule()

    for name, task in BACKUP_ENTRIES.items():
        assert schedule[name]["task"] == task


def test_off_schedules_neither_entry(backups_off):
    schedule = settings_module.get_celery_beat_schedule()

    assert schedule.keys().isdisjoint(BACKUP_ENTRIES)
    assert "monitor-system-health" in schedule


def test_the_setting_defaults_to_on():
    assert settings_module.DATABASE_BACKUPS_ENABLED is True
    assert settings_module.CELERY_BEAT_UNSCHEDULED_ENTRIES == ()


def test_the_unscheduled_entries_are_exactly_the_backup_entries():
    assert set(settings_module.DATABASE_BACKUP_BEAT_ENTRIES) == set(
        BACKUP_ENTRIES
    )


def test_on_keeps_every_row():
    _cloned_rows()

    assert delete_unscheduled_periodic_tasks() == 0
    assert PeriodicTask.objects.filter(name__in=BACKUP_ENTRIES).count() == 2


def test_off_deletes_the_backup_rows_and_only_those():
    _cloned_rows()
    PeriodicTasks.objects.all().delete()

    with override_settings(
        CELERY_BEAT_UNSCHEDULED_ENTRIES=tuple(BACKUP_ENTRIES)
    ):
        assert delete_unscheduled_periodic_tasks() == 2

    assert not PeriodicTask.objects.filter(
        name__in=BACKUP_ENTRIES, enabled=True
    ).exists()
    assert PeriodicTask.objects.filter(name="monitor-system-health").exists()
    assert PeriodicTasks.last_change() is not None


def test_beat_start_runs_the_cleanup():
    from celery.signals import beat_init

    _cloned_rows()

    with override_settings(
        CELERY_BEAT_UNSCHEDULED_ENTRIES=tuple(BACKUP_ENTRIES)
    ):
        beat_init.send(sender=celery_app)

    assert not PeriodicTask.objects.filter(name__in=BACKUP_ENTRIES).exists()
