"""``DATABASE_BACKUPS_ENABLED`` decides whether the nightly dump is scheduled.

Off must mean no dump, no dump pruning, and no leftover beat row, because a
database cloned from production brings the rows along.
"""

from __future__ import annotations

import pytest
from django.conf import settings
from django.test import override_settings
from django_celery_beat.models import IntervalSchedule, PeriodicTask

import settings as settings_module
from core.beat import delete_undeclared_periodic_tasks

pytestmark = pytest.mark.django_db

BACKUP_ENTRIES = {
    "scheduled-database-backup": "core.tasks.scheduled_database_backup",
    "cleanup-old-backups": "core.tasks.cleanup_old_backups",
}


@pytest.fixture
def backups_on(monkeypatch):
    monkeypatch.setattr(settings_module, "DATABASE_BACKUPS_ENABLED", True)


@pytest.fixture
def backups_off(monkeypatch):
    monkeypatch.setattr(settings_module, "DATABASE_BACKUPS_ENABLED", False)


def _cloned_backup_rows() -> None:
    hourly = IntervalSchedule.objects.create(
        every=1, period=IntervalSchedule.HOURS
    )
    for name, task in BACKUP_ENTRIES.items():
        PeriodicTask.objects.create(name=name, task=task, interval=hourly)


def test_on_schedules_both_entries(backups_on):
    schedule = settings_module.get_celery_beat_schedule()

    for name, task in BACKUP_ENTRIES.items():
        assert schedule[name]["task"] == task


def test_off_schedules_neither_entry(backups_off):
    schedule = settings_module.get_celery_beat_schedule()

    assert schedule.keys().isdisjoint(BACKUP_ENTRIES)
    assert "monitor-system-health" in schedule


def test_the_suite_runs_with_backups_on():
    # ``tests/settings.py`` pins the production value before
    # ``settings.py`` is imported, so no ``.env`` can flip it here.
    assert settings_module.DATABASE_BACKUPS_ENABLED is True


def test_the_switch_covers_exactly_the_backup_entries():
    assert set(settings_module.DATABASE_BACKUP_BEAT_ENTRIES) == set(
        BACKUP_ENTRIES
    )


def test_on_keeps_the_rows_a_clone_brings():
    _cloned_backup_rows()

    assert delete_undeclared_periodic_tasks() == 0
    assert PeriodicTask.objects.filter(name__in=BACKUP_ENTRIES).count() == 2


def test_off_deletes_the_rows_a_clone_brings():
    _cloned_backup_rows()
    without_backups = {
        name: entry
        for name, entry in settings.CELERY_BEAT_SCHEDULE.items()
        if name not in BACKUP_ENTRIES
    }

    with override_settings(CELERY_BEAT_SCHEDULE=without_backups):
        assert delete_undeclared_periodic_tasks() == 2

    assert not PeriodicTask.objects.filter(name__in=BACKUP_ENTRIES).exists()
