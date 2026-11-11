"""The beat scheduler: the schedule from settings, the state from the database.

Celery decides when an entry is due, through ``crontab``. This adds what a
restart and the admin need:

- every row the schedule does not declare is deleted on start, so a removed
  entry stops firing;
- a run is saved as soon as it is sent, so a restart sends what it missed
  exactly once and repeats nothing;
- switching an entry off or on in the admin reaches the running process;
- a publish that fails is sent again instead of waiting for the next slot, so
  a broker restart cannot lose a daily job, and it waits on its own: an entry
  that cannot be sent never holds up the others.

Run one beat at a time: nothing here stops two from sending the same tick, and
the Deployment is one replica with the ``Recreate`` strategy.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, override

from celery.beat import ScheduleEntry, Scheduler, SchedulingError
from celery.schedules import schedstate
from django.db import DatabaseError, InterfaceError, close_old_connections

from scheduler.models import PeriodicTask

logger = logging.getLogger(__name__)

#: How often the running process reads the admin's switches. Beat wakes at
#: least once a minute for the every-minute entries, so a change lands within
#: about a minute however low this is.
STATE_REFRESH_SECONDS = 5.0
#: How long an entry waits before it is sent again after its publish failed.
PUBLISH_RETRY_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class _Run:
    at: datetime
    count: int


class RetryingEntry(ScheduleEntry):
    """An entry that is not due again until its failed publish has waited.

    It lives on the entry, and not in the scheduler, because ``populate_heap``
    asks the entry directly: the entries that are really due sort ahead of it.
    """

    def __init__(
        self, *args: Any, retry_at: float = 0.0, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.retry_at = retry_at

    @override
    def is_due(self) -> schedstate:
        if (wait := self.retry_at - time.monotonic()) > 0:
            return schedstate(False, wait)
        return super().is_due()


class CodeOwnedScheduler(Scheduler):
    Entry = RetryingEntry

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Set before ``super().__init__``, which calls ``setup_schedule``.
        self._declared: dict[str, ScheduleEntry] = {}
        self._unsaved_runs: dict[str, _Run] = {}
        self._state_read_at = 0.0
        self._heap_is_stale = False
        super().__init__(*args, **kwargs)

    @override
    def setup_schedule(self) -> None:
        """Build the entries, then make the rows agree with them.

        A database that cannot be reached raises: the container restarts, and
        beat never runs on a schedule it could not read.
        """
        # In this order: ``merge_inplace`` drops whatever the settings do not
        # name, and ``install_default_entries`` adds celery.backend_cleanup.
        self.merge_inplace(self.app.conf.beat_schedule)
        self.install_default_entries(self.data)
        self._declared = {
            name: self._named(entry) for name, entry in self.data.items()
        }
        self.data = {}

        close_old_connections()
        undeclared = PeriodicTask.objects.exclude(name__in=self._declared)
        if names := list(undeclared.values_list("name", flat=True)):
            undeclared.delete()
            logger.info(
                "Deleted periodic tasks the schedule does not declare",
                extra={"names": names},
            )
        PeriodicTask.objects.bulk_create(
            [PeriodicTask(name=name) for name in self._declared],
            ignore_conflicts=True,
        )
        self._apply_state()

    @override
    def tick(self, *args: Any, **kwargs: Any) -> float:
        self._refresh_state()
        interval = super().tick(*args, **kwargs)
        if self._heap_is_stale:
            # ``tick`` has just pushed the entry for its next slot.
            self._heap_is_stale = False
            self._heap = None
        return interval

    @override
    def apply_async(
        self,
        entry: ScheduleEntry,
        producer: Any = None,
        advance: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Send, then save the run: a crash in between repeats, never loses."""
        try:
            result = super().apply_async(entry, producer, advance, **kwargs)
        except SchedulingError:
            # ``tick`` already moved this entry on to its next slot.
            entry.retry_at = time.monotonic() + PUBLISH_RETRY_SECONDS
            self.schedule[entry.name] = entry
            self._heap_is_stale = True
            raise
        sent = self.schedule[entry.name]
        self._unsaved_runs[entry.name] = _Run(
            sent.last_run_at, sent.total_run_count
        )
        self.sync()
        return result

    @override
    def sync(self) -> None:
        """Save the runs not saved yet. A failure keeps them for next time."""
        try:
            while self._unsaved_runs:
                name, run = next(iter(self._unsaved_runs.items()))
                PeriodicTask.objects.filter(name=name).update(
                    last_run_at=run.at, total_run_count=run.count
                )
                del self._unsaved_runs[name]
        except DatabaseError, InterfaceError:
            logger.warning(
                "Could not save periodic task runs; trying again",
                extra={"names": list(self._unsaved_runs)},
                exc_info=True,
            )

    def _named(self, entry: ScheduleEntry) -> ScheduleEntry:
        """Carry the entry's name on its messages, for the Task Results list."""
        options = entry.options
        return self.Entry(
            **dict(
                entry,
                options={
                    **options,
                    "headers": {
                        **options.get("headers", {}),
                        "periodic_task_name": entry.name,
                    },
                },
            )
        )

    def _refresh_state(self) -> None:
        """Follow the admin's switches. A database outage changes nothing."""
        now = time.monotonic()
        if now - self._state_read_at < STATE_REFRESH_SECONDS:
            return
        self._state_read_at = now
        try:
            close_old_connections()
            self._apply_state()
            self.sync()
        except DatabaseError, InterfaceError:
            logger.warning(
                "Could not read periodic task state; keeping the last one",
                exc_info=True,
            )

    def _apply_state(self) -> None:
        """Add the entries just switched on, drop the ones just switched off.

        A changed key set is what makes ``tick`` rebuild its heap. An entry
        already running is left alone: memory is ahead of its row.
        """
        rows = PeriodicTask.objects.filter(name__in=self._declared)
        for row in rows.only(
            "name", "enabled", "resumed_at", "last_run_at", "total_run_count"
        ):
            if not row.enabled:
                self.schedule.pop(row.name, None)
            elif row.name not in self.schedule:
                self.schedule[row.name] = self._resumed(row)

    def _resumed(self, row: PeriodicTask) -> ScheduleEntry:
        # Celery's crontab reads the hour off this value and leaves an aware
        # datetime in its own zone, so it must not be in the database's.
        counted_from = row.counted_from
        return self.Entry(
            **dict(
                self._declared[row.name],
                last_run_at=counted_from.astimezone(self.app.timezone)
                if counted_from
                else None,
                total_run_count=row.total_run_count,
            )
        )
