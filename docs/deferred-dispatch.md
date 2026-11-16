# Tasks queued after a commit

`tenant.celery.dispatch_on_commit` queues a Celery task once the surrounding
transaction has committed, with the store's schema pinned at the moment of the
call. Every task that follows a change (an e-mail, a live notification, an
attachment scan) goes through it.

## When the broker is unreachable

The change is already committed when the publish runs, so raising can only
answer `500` for work that was done. A publish that fails with a network error
(`core.retry.TRANSIENT_NETWORK_ERRORS`: the broker, Redis, a socket) therefore
never reaches the caller:

1. it is logged at `ERROR` (`Publishing <task> failed after its change
   committed`, with the task and the store);
2. it is kept as a `tenant.DeferredDispatch` row in `public` (the task name,
   `args`, `kwargs`, headers including the schema, and the options), so it is
   not lost. If even the row cannot be written, the log line says what the work
   was, with its secrets masked (`core.tasks.safe_task_arguments`, the same mask
   as a failed task's);
3. `tenant.tasks.redispatch_deferred_tasks` (beat, every minute) publishes the
   rows again and deletes each once its task is out. A row that fails again
   waits `DEFERRED_DISPATCH_BACKOFF_SECONDS * 2**attempts` (capped by
   `DEFERRED_DISPATCH_BACKOFF_CAP_SECONDS`); when the broker is still down the
   run stops after the first row instead of waiting out the timeout for each. A
   row older than `DEFERRED_DISPATCH_RETENTION_DAYS` is deleted with an `ERROR`
   (the work was not done) and its masked payload in the log.

## When the broker blocks publishers

While RabbitMQ blocks publishers (a memory or disk alarm) it stops reading a
connection as soon as that connection publishes, and sends nothing back but
`connection.blocked`, on that connection alone: the publish confirm never comes,
and neither does the `Close-Ok` kombu waits for when it closes the connection,
which a publish `timeout=` does not cover. A connection that has not published
since the alarm started is told nothing (measured on RabbitMQ 4.3.7: an idle
connection and one opened during the alarm received no frame until each
published). Without a bound a request that published waited without end (#509).

`core.celery.GrooveShopCelery.connection_for_write` opens every connection the
app publishes on (the producer pool behind tasks and control commands, beat's
producer, a worker's event dispatcher) with `core.broker.PublishingTransport`.
Its connections confirm every publish and keep one state per process:

- `connection.blocked` holds the process off the broker for
  `BROKER_BLOCKED_HOLD_SECONDS` (60 s; the settings refuse anything not above 0,
  which would let events and heartbeats reconnect in a loop). The publish waiting for its confirm ends
  at once with py-amqp's `Blocked`, a recoverable connection error: kombu drops
  that connection without a close handshake and retries on a new one (the one
  retry of `CELERY_TASK_PUBLISH_RETRY_POLICY`, or kombu's endless retry for
  events, heartbeats and control commands).
- While the hold lasts, a publish is refused before it writes, with
  `BrokerBlocked`, a kombu `OperationalError` that kombu does not retry and
  `dispatch_on_commit` keeps as a row. The retry above ends there.
  `GrooveShopCelery.send_task` refuses a task before it takes a pooled
  connection, since kombu replaces a pooled connection after any failed
  publish.
- `connection.unblocked` ends the hold. RabbitMQ sends it only on the
  connection it blocked, which kombu has dropped, so once the hold passes the
  next publish first asks again: an empty message to the default exchange with a
  routing key no queue can have (`amq.` names are reserved), which RabbitMQ
  drops. Blocked again, the hold starts over and the real message is never
  written; confirmed, publishing resumes. A question that fails for another
  reason (a connection a restarted broker reset) leaves the hold passed, so
  kombu's retry asks again on a new connection. One thread asks at a time; the
  others are refused meanwhile.
- A task published by a task the worker is running (`self.retry()`, `.delay()`,
  a `dispatch_on_commit` inside a task) is never refused
  (`GrooveShopCelery.send_task`, `core.broker.waiting_through_blocks`).
  `Task.retry` turns a failed publish into `Reject(requeue=False)`, and an
  `acks_late` message rejected that way is dropped: a refusal there loses the
  task. Instead the message is written once and the publish waits for its
  confirm through the block, as it did before #509, so the task runs once after
  the alarm clears. The cost: a worker child that publishes during an alarm is
  held until it clears, bounded by the task's time limit
  (`CELERY_TASK_SOFT_TIME_LIMIT`), as before #509.

So during an alarm a process writes one message, the publish that found out, and
then nothing but one question per hold. Each question leaves one connection the
broker no longer reads until the alarm clears; the hold is long so that a broker
short of memory collects few of them, at the cost of publishing resuming up to a
minute after the alarm clears (rows are swept, and beat sends a missed entry
again after `scheduler.beat.PUBLISH_RETRY_SECONDS`). The hold does not grow
with each block: fewer connections would be left, but after a long alarm
publishing would resume minutes after it cleared.

`read_timeout` (`BROKER_PUBLISH_READ_TIMEOUT`, 5 s, applied as `SO_RCVTIMEO`)
bounds a reply the broker never sends without saying why, such as the confirm
of a connection it blocked without `connection.blocked`. Writes need no option
of their own: the `TCP_USER_TIMEOUT` py-amqp sets by default already ends a
write the broker stops taking. A read that timed out is recorded as
`OperationalError: timed out` (`tenant.celery.describe_error`: kombu's own text
for it is empty).

A worker's consuming connection (`connection_for_read`) keeps kombu's own
transport. A read timeout there makes the worker drop the connection, and
RabbitMQ then redelivers every task the worker is still running
(`task_acks_late`). `tests/unit/core/test_broker_blocked.py` drives Celery's
publish paths against a fake broker that blocks like RabbitMQ and counts what it
received; `tests/unit/core/test_celery_publishing_connections.py` checks the
read and write bounds against a peer that never answers.

Every synchronous reply on a publishing connection must arrive within the read
timeout: a confirm, a declare, a channel open. Measured through the app's
publishing connection, 2000 persistent 1 KiB messages to a durable queue, with
the broker limited to half a CPU as in production: median 1.0 ms, p99 42 ms,
slowest 49 ms. The 5 s default leaves a hundred times that.

Measured on Linux against RabbitMQ 4.3.7 with its memory alarm on, one web
process: a `dispatch_on_commit`, then five `apply_async` and two
`control.ping(timeout=3)`:

| | Before (on_blocked ended the confirm wait, connection kept) | `core.broker` |
|---|---|---|
| `dispatch_on_commit` | 10.5 s, row kept | 0.02 s, row kept |
| each `apply_async` | 10.2 s | 0.00 s, nothing written |
| each `control.ping` | 10.2 s | 0.04 s |
| connections opened during the alarm | 9, all held by the broker until it cleared | 4 |
| task messages delivered after the alarm | 6 | 1 (the publish that found out) |

Beat, with an entry due every 10 s, through a 120 s alarm: before, each attempt
after the first wrote on the kept blocked connection, waited out the read
timeout, reconnected and wrote again (8 copies delivered, 4 connections opened);
now 1 copy and 2 connections. A worker running with `--events` through a 161 s
alarm opened 15 connections before (its heartbeats reconnecting in a loop) and
3 now; in both runs an acks_late task running across the alarm ran once.

The one message written is delivered once the alarm clears, and the row the
sweep publishes is one more run. A task published through the helper must
therefore be safe to run more than once, as it already must be for a row the
sweep replays.

The read timeout is set on Linux only, where every deployment runs: py-amqp packs
the value as a POSIX `timeval`, which Windows reads as milliseconds, so a Windows
host has no read bound (the hold and the refusal still apply).

## One sweep at a time

One sweep runs at a time, and it publishes outside any transaction. A publish to
a broker that stops answering holds it for the read timeout above. A transaction held across that
could be ended by `idle_in_transaction_session_timeout` and send every row
already published a second time, and any lease could be outlived. So a sweep
holds a session-level advisory lock (`pg_try_advisory_lock`), and one that finds it held does nothing.
The lock ends with the session if the worker dies; the next sweep then sends again
at most the one row that was going out.

A healthy sweep takes seconds and beat sends one a minute, so a sweep that finds
the lock held logs a WARNING with how many rows are due and the oldest. The
connection pool resets nothing on a returned connection, so an unlock that
releases nothing is an ERROR: a lock left held would stop every sweep until the
pool retires that connection (an hour by default).

A failed publish holds its caller for all of Celery's connection retries, and an
order makes six of them, so one failure tells this process to keep the next ones
without trying for `DEFERRED_DISPATCH_BROKER_DOWN_SECONDS`. Measured on a local
stack with the broker's port closed, on Windows where a refused connection is
slow: one order held its request 52.7 s without this, about 8.8 s for each
publish. The rows it keeps go out through the same sweep.

Celery raises `OperationalError` for every connection error of the transport,
which for RabbitMQ includes any `OSError`, a TLS failure and a nacked message, so
those are kept too. Anything else, such as a `TypeError` from bad arguments, is a
bug and still raises. There is no metrics stack on this
platform: the number of `DeferredDispatch` rows is the count of publishes
waiting, and each failure is an `ERROR` log line.

`robust=` is gone from `dispatch_on_commit`: every caller has this behaviour.

## Which publishes go through it

A publish that follows a committed change goes through the helper, whether it
is called from a request, a signal, a task or the admin: the order's emails,
the carrier voucher and shipment tasks (`ShippingCarrierInterface.
dispatch_create_shipment_task` implementations publish with it and pass the
`schema_name` the caller captured), the myDATA chain in `src/order/tasks.py`, the
data export and account erasure, comment-liked notifications, search
reindexing, the cache and edge-cache purges, the offer refresh, the
recommendation impression, the carrier arrival notification and the admin's
voucher / shipment reissue. A hook that runs inside another `on_commit` hook
(the loyalty tier receivers, `_flush` in `src/core/cache/invalidation.py`) is
handed the schema registered with the change and passes it as `schema_name=`,
because the connection no longer says which store it was.

Two things keep no row, on purpose:

- **Analytics beacons** (`search_click`, `recommendation_event`, and the query
  `SearchAnalyticsMiddleware` records) go through
  `tenant.celery.publish_or_drop`. They are open to anonymous traffic, so a row
  per beacon in an outage would turn that traffic into database rows. The
  request is answered as usual and the event is dropped. They share the window
  above, so a visitor does not wait for a broker known to be down, and only the
  failure that opens it is logged.
- **Publishes that are their own repair or have nothing to keep**: a webhook
  whose redelivery is the repair (BoxNow, Resend), a staff click that
  committed nothing (an error answers the click, and a destructive action
  must not run days later), a sweep or poll task that runs again on its own
  schedule, a Celery `chain` (it cannot be a row), the beat fan-out that
  resets the demo stores, and `redispatch_deferred_tasks` itself, whose
  republish is the repair.

A task that is published through the helper and that publishes the next link
of a chain (`send_invoice_to_mydata` then `send_invoice_email`) keeps its own
short-circuits (the MARK check, the reserved-email flag): a row replayed by the
sweep runs the task again, so the task must be safe to run twice. A row older
than `DEFERRED_DISPATCH_RETENTION_DAYS` is dropped with an `ERROR`, which for a
myDATA document is a filing that did not happen: that log line is what to act
on.

A step that must run only after a publish was handed over is the helper's
`then` (`shipping_acs.services.AcsService._maybe_notify_arrival` passes the
`arrival_notified_at` mark). It runs in the pinned schema once the task is
published or its row is written, and not when the row could not be written
either, so the mark never outlives a message that was neither sent nor kept.

A row holds the task's arguments as they are until the sweep sends it, so during
an outage it can hold a mail's reset link or login code, as the broker's own queue
would, and a database dump taken then captures them. The logs never show them.

## What the table cannot see

A worker killed between the commit and the publish loses the dispatch without
raising. A task whose work is a STATE of a row is covered by a sweep over that
state, which is the guarantee for it:

| Task | Sweep |
|---|---|
| `contact.tasks.scan_contact_attachment` | `rescan_pending_attachments` queues every attachment still `PENDING` after `CONTACT_SCAN_PENDING_AFTER_MINUTES` (a fan-out beat entry every 30 minutes) |

A task that has no such state (a transactional e-mail, a live notification) is
covered by the table for a publish that fails; a process killed in that
instant is the one gap, and it is the same one a database commit followed by a
crash has anywhere without a transactional outbox.
