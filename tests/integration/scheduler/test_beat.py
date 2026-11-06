"""The scheduler, driven tick by tick on a clock the test owns.

Only the broker is replaced. Celery decides what is due through its real
``crontab`` and ``Scheduler.tick``, and the state lives in the real table.
"""

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

import pytest
from celery import Celery
from celery.schedules import crontab
from django.conf import settings
from django.db import OperationalError, connection
from kombu.exceptions import OperationalError as BrokerError

from core.celery import celery_app
from scheduler import beat
from scheduler.beat import PUBLISH_RETRY_SECONDS, CodeOwnedScheduler
from scheduler.models import PeriodicTask

pytestmark = pytest.mark.django_db

ATHENS = ZoneInfo("Europe/Athens")
BACKEND_CLEANUP = "celery.backend_cleanup"


class Harness:
    """One beat process at a time, against one clock and one fake broker."""

    def __init__(self, monkeypatch):
        self.now = datetime(2026, 11, 2, 10, 0, tzinfo=UTC)
        self.sent: list[tuple[str, datetime]] = []
        self.messages: list[dict] = []
        self.broker_down = False
        self.app = Celery("harness", set_as_current=False)
        self.app.conf.timezone = "Europe/Athens"
        self.app.conf.result_expires = None
        self.schedule: dict = {}
        harness = self

        def send_task(_self, name, args=None, kwargs=None, **options):
            if harness.broker_down:
                raise BrokerError("broker unreachable")
            harness.sent.append((name.removeprefix("tasks."), harness.now))
            harness.messages.append(options)

        monkeypatch.setattr(CodeOwnedScheduler, "send_task", send_task)
        # The test's transaction lives on this connection.
        monkeypatch.setattr(beat, "close_old_connections", lambda: None)
        monkeypatch.setattr(beat, "STATE_REFRESH_SECONDS", 0.0)

    def clock(self):
        return self.now.astimezone(ATHENS)

    def every(self, name, **fields):
        self.schedule[name] = {
            "task": f"tasks.{name}",
            "schedule": crontab(nowfun=self.clock, **fields),
        }

    def start(self) -> CodeOwnedScheduler:
        self.app.conf.beat_schedule = self.schedule
        scheduler = CodeOwnedScheduler(app=self.app)
        scheduler.__dict__["producer"] = None
        return scheduler

    def run(self, scheduler, *, until, step=timedelta(seconds=30)):
        """Tick as ``Service.start`` does: again at once while it returns 0."""
        while self.now < until:
            while scheduler.tick() == 0:
                pass
            self.now += step


@pytest.fixture
def harness(monkeypatch):
    return Harness(monkeypatch)


def _names(harness):
    return [name for name, _ in harness.sent]


class TestRows:
    def test_a_row_the_schedule_does_not_declare_is_deleted_and_never_sent(
        self, harness
    ):
        PeriodicTask.objects.create(
            name="retired", last_run_at=harness.now - timedelta(days=1)
        )
        harness.every("kept", minute="0")

        scheduler = harness.start()
        harness.run(scheduler, until=harness.now + timedelta(hours=2))

        assert set(PeriodicTask.objects.values_list("name", flat=True)) == {
            "kept"
        }
        assert "retired" not in _names(harness)

    def test_the_real_schedule_ends_up_as_declared_plus_the_cleanup(
        self, monkeypatch
    ):
        monkeypatch.setattr(beat, "close_old_connections", lambda: None)
        PeriodicTask.objects.create(name="retired")

        CodeOwnedScheduler(app=celery_app)

        assert set(PeriodicTask.objects.values_list("name", flat=True)) == {
            *settings.CELERY_BEAT_SCHEDULE,
            BACKEND_CLEANUP,
        }

    def test_starting_twice_changes_nothing(self, harness):
        harness.every("job", minute="0")
        harness.start()
        harness.start()

        assert PeriodicTask.objects.count() == 1

    def test_a_lazy_scheduler_touches_nothing(self, harness):
        harness.every("job", minute="0")

        CodeOwnedScheduler(app=harness.app, lazy=True)

        assert not PeriodicTask.objects.exists()


class TestRunsAcrossRestarts:
    def test_a_new_entry_waits_for_its_next_slot(self, harness):
        harness.every("job", minute="0")

        scheduler = harness.start()
        harness.run(scheduler, until=harness.now + timedelta(minutes=30))

        assert harness.sent == []

    def test_a_slot_missed_while_down_is_sent_once_on_start(self, harness):
        harness.every("job", minute="*/5")
        harness.now = datetime(2026, 11, 2, 10, 1, tzinfo=UTC)
        PeriodicTask.objects.create(
            name="job", last_run_at=datetime(2026, 11, 2, 9, 40, tzinfo=UTC)
        )

        scheduler = harness.start()
        harness.run(scheduler, until=harness.now + timedelta(minutes=1))

        # Four slots passed. One message covers them.
        assert _names(harness) == ["job"]

    def test_a_restart_repeats_nothing(self, harness):
        harness.every("job", minute="*/5")
        first = harness.start()
        harness.run(first, until=harness.now + timedelta(minutes=11))
        sent = len(harness.sent)
        assert sent == 2

        second = harness.start()
        harness.run(second, until=harness.now + timedelta(minutes=2))

        assert len(harness.sent) == sent

    def test_the_run_is_saved_as_soon_as_it_is_sent(self, harness, monkeypatch):
        """A hard kill loses no state: nothing waits for a later sync.

        One tick, with Celery's periodic sync just done and the state refresh
        out of reach, so only the save on send can have written the row.
        """
        monkeypatch.setattr(beat, "STATE_REFRESH_SECONDS", float("inf"))
        harness.every("job", minute="*/5")
        harness.now = datetime(2026, 11, 2, 10, 4, tzinfo=UTC)
        scheduler = harness.start()
        scheduler._do_sync()
        harness.now = datetime(2026, 11, 2, 10, 5, 1, tzinfo=UTC)

        scheduler.tick()

        assert _names(harness) == ["job"]
        row = PeriodicTask.objects.get(name="job")
        assert row.total_run_count == 1
        assert row.last_run_at == harness.sent[0][1]

    def test_a_slot_is_read_in_the_schedules_time_zone(self, harness):
        """16:30 Athens is 14:30Z: the row's UTC hour must not decide."""
        harness.every("pickup", hour="16", minute="30")
        PeriodicTask.objects.create(
            name="pickup", last_run_at=datetime(2026, 11, 1, 14, 30, tzinfo=UTC)
        )

        harness.now = datetime(2026, 11, 2, 14, 29, tzinfo=UTC)
        harness.run(harness.start(), until=harness.now + timedelta(seconds=30))
        assert harness.sent == []

        harness.now = datetime(2026, 11, 2, 14, 31, 40, tzinfo=UTC)
        harness.run(harness.start(), until=harness.now + timedelta(seconds=30))
        assert _names(harness) == ["pickup"]


class TestSwitches:
    def test_off_reaches_the_running_process(self, harness):
        harness.every("job", minute="*/5")
        scheduler = harness.start()
        harness.run(scheduler, until=harness.now + timedelta(minutes=6))
        assert len(harness.sent) == 1

        PeriodicTask.objects.filter(name="job").switch_off()
        harness.run(scheduler, until=harness.now + timedelta(minutes=30))

        assert len(harness.sent) == 1

    def test_on_sends_no_slot_it_missed_while_off(self, harness):
        harness.every("job", minute="*/5")
        scheduler = harness.start()
        harness.run(scheduler, until=harness.now + timedelta(minutes=6))
        PeriodicTask.objects.filter(name="job").switch_off()
        harness.run(scheduler, until=harness.now + timedelta(minutes=30))
        sent = len(harness.sent)
        harness.now += timedelta(minutes=2)

        # ``switch_on`` stamps the real clock, which this test owns separately.
        PeriodicTask.objects.filter(name="job").update(
            enabled=True, resumed_at=harness.now
        )
        harness.run(scheduler, until=harness.now + timedelta(seconds=30))
        assert len(harness.sent) == sent

        harness.run(scheduler, until=harness.now + timedelta(minutes=6))
        assert len(harness.sent) == sent + 1

    def test_off_survives_a_restart(self, harness):
        harness.every("job", minute="*/5")
        PeriodicTask.objects.create(name="job", enabled=False)

        harness.run(harness.start(), until=harness.now + timedelta(minutes=30))

        assert harness.sent == []
        assert PeriodicTask.objects.get(name="job").enabled is False


class TestFailures:
    def test_a_failed_publish_is_sent_again_and_saved_only_then(self, harness):
        """A broker restart at 16:30 must not lose the day's pickup list."""
        # 12:00 Athens is 10:00Z.
        harness.every("job", hour="12", minute="0")
        harness.now = datetime(2026, 11, 2, 9, 59, tzinfo=UTC)
        scheduler = harness.start()
        harness.now = datetime(2026, 11, 2, 10, 0, 30, tzinfo=UTC)
        harness.broker_down = True

        assert scheduler.tick() == PUBLISH_RETRY_SECONDS
        row = PeriodicTask.objects.get(name="job")
        assert row.last_run_at is None
        assert harness.sent == []

        harness.broker_down = False
        harness.run(scheduler, until=harness.now + timedelta(seconds=30))

        assert _names(harness) == ["job"]
        assert PeriodicTask.objects.get(name="job").total_run_count == 1

    def test_an_unreachable_database_does_not_stop_sending(
        self, harness, monkeypatch
    ):
        harness.every("job", minute="*/5")
        scheduler = harness.start()

        def down(*args, **kwargs):
            raise OperationalError("database unreachable")

        with monkeypatch.context() as outage:
            outage.setattr(CodeOwnedScheduler, "_apply_state", down)
            outage.setattr(PeriodicTask.objects, "filter", down)
            harness.run(scheduler, until=harness.now + timedelta(minutes=6))
            # Raising here would report a message that was sent as failed.
            scheduler.sync()

        assert len(harness.sent) == 1
        assert PeriodicTask.objects.get(name="job").total_run_count == 0

        # The run it could not save is saved once the database is back.
        harness.run(scheduler, until=harness.now + timedelta(seconds=30))

        assert PeriodicTask.objects.get(name="job").total_run_count == 1


class TestDaylightSaving:
    @staticmethod
    def _replay(harness, start, end):
        for name, fields in {
            "every_5": {"minute": "*/5"},
            "hourly": {"minute": "0"},
            "at_0300": {"hour": "3", "minute": "0"},
            "at_0330": {"hour": "3", "minute": "30"},
            "at_0400": {"hour": "4", "minute": "0"},
        }.items():
            harness.every(name, **fields)
        harness.now = start
        harness.run(harness.start(), until=end)

        fires: dict[str, list[datetime]] = {}
        for name, at in harness.sent:
            fires.setdefault(name, []).append(at)
        return fires

    @staticmethod
    def _gaps(times):
        return [int((b - a).total_seconds() / 60) for a, b in pairwise(times)]

    def test_autumn_skips_the_repeated_hour_and_runs_nothing_twice(
        self, harness
    ):
        """Celery's behaviour, recorded so a change in it is noticed.

        Clocks go back at 01:00Z, from 04:00 to 03:00.
        """
        fires = self._replay(
            harness,
            datetime(2026, 10, 24, 20, tzinfo=UTC),
            datetime(2026, 10, 25, 6, tzinfo=UTC),
        )

        assert max(self._gaps(fires["every_5"])) == 65
        assert min(self._gaps(fires["every_5"])) >= 5
        assert max(self._gaps(fires["hourly"])) == 120
        assert [len(fires[n]) for n in ("at_0300", "at_0330", "at_0400")] == [
            1,
            1,
            1,
        ]

    def test_spring_runs_each_job_once_and_keeps_the_cadence(self, harness):
        """Clocks go forward at 01:00Z, from 03:00 to 04:00."""
        fires = self._replay(
            harness,
            datetime(2027, 3, 27, 20, tzinfo=UTC),
            datetime(2027, 3, 28, 6, tzinfo=UTC),
        )

        assert max(self._gaps(fires["every_5"])) == 5
        assert max(self._gaps(fires["hourly"])) == 60
        assert [len(fires[n]) for n in ("at_0300", "at_0330", "at_0400")] == [
            1,
            1,
            1,
        ]


class TestMessages:
    def test_a_message_names_its_entry_and_keeps_its_options(self, harness):
        harness.every("job", minute="*/5")
        harness.schedule["job"]["options"] = {"queue": "celery", "expires": 300}

        harness.run(harness.start(), until=harness.now + timedelta(minutes=6))

        message = harness.messages[0]
        assert message["queue"] == "celery"
        assert message["expires"] == 300
        assert message["headers"]["periodic_task_name"] == "job"


class TestAdoptingThePreviousScheduler:
    @staticmethod
    def _old_table(name, last_run_at, count):
        with connection.cursor() as cursor:
            cursor.execute(
                "CREATE TABLE django_celery_beat_periodictask ("
                "name varchar(200), last_run_at timestamptz, "
                "total_run_count integer)"
            )
            cursor.execute(
                "INSERT INTO django_celery_beat_periodictask VALUES (%s, %s, %s)",
                [name, last_run_at, count],
            )

    def test_a_slot_the_old_beat_sent_after_the_copy_is_not_sent_again(
        self, harness
    ):
        harness.every("job", minute="*/5")
        harness.now = datetime(2026, 11, 2, 10, 6, tzinfo=UTC)
        PeriodicTask.objects.create(
            name="job",
            last_run_at=datetime(2026, 11, 2, 9, 55, tzinfo=UTC),
            total_run_count=7,
        )
        self._old_table("job", datetime(2026, 11, 2, 10, 5, tzinfo=UTC), 9)

        harness.run(harness.start(), until=harness.now + timedelta(seconds=30))

        assert harness.sent == []
        assert PeriodicTask.objects.get(name="job").total_run_count == 9

    def test_it_never_moves_a_run_backwards(self, harness):
        harness.every("job", minute="*/5")
        newer = datetime(2026, 11, 2, 10, 5, tzinfo=UTC)
        PeriodicTask.objects.create(
            name="job", last_run_at=newer, total_run_count=9
        )
        self._old_table("job", datetime(2026, 11, 2, 9, 0, tzinfo=UTC), 7)

        harness.start()

        row = PeriodicTask.objects.get(name="job")
        assert row.last_run_at == newer
        assert row.total_run_count == 9
