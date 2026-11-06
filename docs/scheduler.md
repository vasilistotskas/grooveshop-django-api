# The scheduler

`celery beat` runs the periodic tasks, with our own scheduler
(`scheduler/beat.py: CodeOwnedScheduler`, a `celery.beat.Scheduler` subclass)
in place of django-celery-beat, which we removed because its `Django<x` cap
blocked every Django upgrade until it published a release.

## Where things live

| What | Where |
|---|---|
| What runs and when | `CELERY_BEAT_SCHEDULE` (`settings.py: get_celery_beat_schedule()`) |
| Whether a slot is due | Celery's `crontab` |
| Switched on or off, last run, run count | `scheduler.PeriodicTask`, one row per entry, in `public` |

To change when a job runs, change the settings and deploy. To remove a job,
remove its entry: beat deletes every row the settings do not declare when it
starts, so no migration is needed. `celery.backend_cleanup` is the one entry
not in the settings, because Celery installs it while
`CELERY_RESULT_EXPIRES` is set.

## What beat guarantees

- **A restart loses nothing.** A run is saved as soon as it is sent, and a new
  pod sends an overdue entry once, however many slots passed. Beat is one pod
  with the `Recreate` strategy, so every deploy leaves a short gap.
- **A restart repeats nothing**, except in the moment between a send and its
  save, if the process dies right there. A task must tolerate that, which
  `CELERY_TASK_ACKS_LATE` requires anyway.
- **A failed publish is retried** every `PUBLISH_RETRY_SECONDS`, so a broker
  restart does not cost a daily job its day.
- **A database outage does not stop sending.** Beat keeps its last known
  switches and saves the runs when the database is back. At start, though, it
  needs the database, and the container restarts until it has it.
- **`options` in an entry apply.** `expires` means a message waiting longer than
  that is discarded, not run. django-celery-beat silently dropped it.

## Time zones

Times are Athens local (`CELERY_TIMEZONE`). Celery's crontab decides around a
clock change:

- **Autumn, 04:00 back to 03:00.** The repeated hour is skipped. On 2026-10-25
  a `*/5` job had a 65-minute gap, a `*/15` job 75 and an hourly job 120. Nothing
  ran twice. `GrooveShopBeatSilent` fires for that hour, truthfully, and needs
  no action.
- **Spring, 03:00 to 04:00.** A job in the missing hour runs once at 04:00.

`tests/integration/scheduler/test_beat.py` replays both, so a Celery upgrade
that changes this fails a test.

## The admin

`Background Jobs → Periodic Tasks` on the platform console reads the schedule,
switches an entry off or on, and runs one by hand. `enabled` survives a restart.
Switching on sends no slot missed while off (`resumed_at`). A run by hand is not
recorded as a run, so it does not move the next slot.

`emails.gates.ScheduleGate` reads `enabled`, and "did it run?" is `last_run_at`.

## Running one beat

Celery has no protection against two beats sending the same tick, and neither
does this. The Deployment has one replica and the `Recreate` strategy, so run it
that way locally too.

## Source

| File | Role |
|---|---|
| `scheduler/beat.py` | The scheduler |
| `scheduler/models.py` | `PeriodicTask`, `switch_on`, `switch_off` |
| `scheduler/admin.py` | The console screen and its actions |
| `settings.py` | `CELERY_BEAT_SCHEDULE`, `CELERY_BEAT_SCHEDULER` |
| `tests/integration/scheduler/` | Tick-by-tick behaviour on a clock the test owns |
