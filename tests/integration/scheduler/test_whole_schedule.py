"""The real schedule through the real scheduler, for five simulated weeks.

A soak of a day or two never reaches the weekly and monthly entries. This runs
every entry production declares across a month boundary and the autumn clock
change, on a clock the test owns, and checks each one fired exactly when an
independent reading of its crontab says it should, and exactly once.
"""

import copy
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from celery import Celery

import settings.schedule as settings_module
from scheduler import beat
from scheduler.beat import CodeOwnedScheduler

pytestmark = pytest.mark.django_db

beat_logger = logging.getLogger("celery.beat")

ATHENS = ZoneInfo("Europe/Athens")
START = datetime(2026, 10, 19, 0, 0, 30, tzinfo=UTC)
END = START + timedelta(weeks=5)
STEP = timedelta(minutes=1)


def _expected(schedule) -> list[datetime]:
    """Each slot of a crontab, read from its fields rather than from Celery."""
    slots = []
    minute = START.replace(second=0) + timedelta(minutes=1)
    # The clock ticks 30 seconds into each minute and stops at ``END``.
    while minute + timedelta(seconds=30) < END:
        local = minute.astimezone(ATHENS)
        if (
            # Celery skips the hour repeated when the clocks go back: recorded
            # in ``test_beat.py``. Nothing else differs from the wall clock.
            not local.fold
            and local.minute in schedule.minute
            and local.hour in schedule.hour
            and local.isoweekday() % 7 in schedule.day_of_week
            and local.day in schedule.day_of_month
            and local.month in schedule.month_of_year
        ):
            slots.append(minute)
        minute += timedelta(minutes=1)
    return slots


def test_every_entry_fires_at_every_slot_exactly_once(monkeypatch):
    clock = {"now": START}
    fired: dict[str, list[datetime]] = {}

    def now():
        return clock["now"].astimezone(ATHENS)

    def send_task(_self, name, args=None, kwargs=None, **options):
        entry = options["headers"]["periodic_task_name"]
        fired.setdefault(entry, []).append(clock["now"].replace(second=0))

    # The entries production declares, wherever the test runs: backups on,
    # and a Kubernetes pod, which declares no Docker-only development entry.
    monkeypatch.setattr(settings_module, "DEBUG", False)
    monkeypatch.setattr(settings_module, "DATABASE_BACKUPS_ENABLED", True)
    monkeypatch.setattr(settings_module, "IS_KUBERNETES", True)
    declared = settings_module.get_celery_beat_schedule()
    schedule = {}
    for name, entry in declared.items():
        crontab = copy.copy(entry["schedule"])
        crontab.nowfun = now
        # A name no task has: ``shared_task`` registers on every app, and
        # the scheduler would publish a real task instead of ``send_task``.
        schedule[name] = {
            **entry,
            "task": f"simulated.{entry['task']}",
            "schedule": crontab,
        }

    app = Celery("whole-schedule", set_as_current=False)
    app.conf.timezone = "Europe/Athens"
    app.conf.result_expires = None
    app.conf.broker_url = "memory://"
    app.conf.beat_schedule = schedule
    monkeypatch.setattr(CodeOwnedScheduler, "send_task", send_task)
    monkeypatch.setattr(beat, "close_old_connections", lambda: None)
    monkeypatch.setattr(beat, "STATE_REFRESH_SECONDS", float("inf"))
    # This checks when entries fire. Saving a run is covered in ``test_beat.py``,
    # and thousands of writes here would outlast the test timeout.
    monkeypatch.setattr(CodeOwnedScheduler, "sync", lambda self: None)
    monkeypatch.setattr(beat_logger, "disabled", True)
    scheduler = CodeOwnedScheduler(app=app)
    scheduler.__dict__["producer"] = None

    while clock["now"] < END:
        while scheduler.tick() == 0:
            pass
        clock["now"] += STEP

    wrong = {}
    for name, entry in schedule.items():
        expected = _expected(entry["schedule"])
        actual = fired.get(name, [])
        if actual != expected:
            wrong[name] = {
                "missed": sorted(set(expected) - set(actual))[:3],
                "extra": sorted(set(actual) - set(expected))[:3],
                "expected": len(expected),
                "fired": len(actual),
            }

    assert wrong == {}
    # It really covered what a short soak cannot.
    assert set(schedule) == set(fired)
    assert any(len(times) <= 6 for times in fired.values())
