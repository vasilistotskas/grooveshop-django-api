"""``DATABASE_BACKUPS_ENABLED`` decides whether the nightly dump is scheduled.

Off must mean no dump and no dump pruning. A database cloned from production
brings the rows along, and beat deletes them because the schedule does not
declare them (``tests/integration/scheduler/test_beat.py``).
"""

# The schedule reads these from its own module, so that is the one
# patched.
import settings.schedule as settings_module

BACKUP_ENTRIES = {
    "scheduled-database-backup": "core.tasks.scheduled_database_backup",
    "cleanup-old-backups": "core.tasks.cleanup_old_backups",
}


def test_on_schedules_both_entries(monkeypatch):
    monkeypatch.setattr(settings_module, "DATABASE_BACKUPS_ENABLED", True)

    schedule = settings_module.get_celery_beat_schedule()

    for name, task in BACKUP_ENTRIES.items():
        assert schedule[name]["task"] == task


def test_off_schedules_neither_entry(monkeypatch):
    monkeypatch.setattr(settings_module, "DATABASE_BACKUPS_ENABLED", False)

    schedule = settings_module.get_celery_beat_schedule()

    assert schedule.keys().isdisjoint(BACKUP_ENTRIES)
    assert "monitor-system-health" in schedule


def test_the_suite_runs_with_backups_on():
    # ``tests/settings.py`` pins the production value before
    # ``settings`` is imported, so no ``.env`` can flip it here.
    assert settings_module.DATABASE_BACKUPS_ENABLED is True


def test_the_switch_covers_exactly_the_backup_entries():
    assert set(settings_module.DATABASE_BACKUP_BEAT_ENTRIES) == set(
        BACKUP_ENTRIES
    )
