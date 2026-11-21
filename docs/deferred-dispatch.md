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
   was, with its secrets masked (`core.monitored_task.safe_task_arguments`, the same mask
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
  `BROKER_BLOCKED_HOLD_SECONDS` (60 s; the settings refuse anything not a finite
  number above 0: 0 would let events and heartbeats reconnect in a loop, an
  endless hold would refuse every publish for good). The publish waiting for its confirm ends
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
  (`CELERY_TASK_SOFT_TIME_LIMIT`), as before #509. The tasks that run every
  minute and always publish (`fanout_*`, `redispatch_deferred_tasks`, builder
  publishes) take every child within about a minute of an alarm, so the
  workers stop draining queues until it clears. An alarm caused by a backlog
  does not clear by itself: free broker memory or raise its watermark.

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

### A worker through a long alarm (#721)

Two things still restarted or stopped a worker during an alarm, and RabbitMQ
then redelivered every `acks_late` task it was running:

- **Heartbeats missed on the event connection.** A worker opens its event
  dispatcher's connection with `CELERY_BROKER_HEARTBEAT` and its event loop
  checks that connection's heartbeats, but reads only the socket the
  dispatcher started with. A block makes kombu replace that socket, and during
  the hold nothing publishes on the new one, so nothing reads it. Once two
  intervals passed without a read, the loop raised "Too many heartbeats
  missed" and restarted the consumer, consuming connection included. With the
  default hold (60 s, exactly two intervals) the question after each hold
  usually read the socket in time, but each hold was a race between the
  heartbeat check (every 10 s: `broker_heartbeat_checkrate` is 3 per 30 s
  interval) and the question, which waits up to 2 s for the next worker
  heartbeat. Two measured four-minute alarms did not lose it; a hold longer
  than two intervals lost every time. `core.broker.PublishingConnection`
  never fails for a heartbeat it missed: nothing reads a publishing connection
  between publishes. It still sends its heartbeats, and RabbitMQ still sends
  its own, which is how the broker closes a connection kombu dropped while the
  broker was not reading it. A dead broker is still found by the next
  publish's write and confirm, and by the consuming connection's heartbeats.
- **A refused question was fatal.** A worker that starts, or restarts its
  consumer, says hello to its neighbours (Celery's `Mingle` step) with a
  control broadcast. During the hold it was refused, and `Mingle` treats any
  error but a connection error as fatal: "Unrecoverable error: BrokerBlocked"
  and the process exited. A pod started during an alarm (a deploy, an
  OOM-kill) restarted in a loop until it cleared.
  `core.celery.GrooveShopControl` answers a query (an `inspect` command in
  `celery.worker.control`: `hello`, `ping`, `stats`, ...) with no replies
  while the process holds, and logs a WARNING: the worker goes on "all
  alone", a ping finds no worker. Such a worker skips `Mingle`'s sync of
  revoked task ids and logical clocks with its neighbours, and `Mingle` does
  not run again once the alarm clears; nothing in this codebase revokes
  tasks. Every other command (`revoke`, `shutdown`, `pool_restart`,
  `rate_limit`, ...) still raises, whether or not it waits for replies: the
  `celery control` command line always does, and an operator must see that
  it was not delivered.

Measured on Linux against RabbitMQ 4.3.7: a worker as in production (`--events`,
prefork, `acks_late`; concurrency 2) running two tasks across an alarm of
about four minutes (three on the release before #719), and a worker started
while the alarm is on:

| | Before #719 | #719 | #719, hold 90 s | Now | Now, hold 90 s |
|---|---|---|---|---|---|
| consuming connection | closed by RabbitMQ ("missed heartbeats from client") a minute in | kept | consumer restarted 68 s in ("Too many heartbeats missed"), then the worker exited (`BrokerBlocked` in `Mingle`) | kept | kept |
| each running task | ran twice | ran once | finished but could not be acked, so it was queued again | ran once | ran once |
| a worker started during the alarm | waits in `Mingle` until it clears | exits at once (code 1) | | ready within a second, "all alone" | |

The one message written is delivered once the alarm clears, and the row the
sweep publishes is one more run. A task published through the helper must
therefore be safe to run more than once, as it already must be for a row the
sweep replays.

The read timeout is set on Linux only, where every deployment runs: py-amqp packs
the value as a POSIX `timeval`, which Windows reads as milliseconds, so a Windows
host has no read bound (the hold and the refusal still apply).

## One dispatch, one task id

`dispatch_on_commit` gives each call a task id of its own and publishes with
it (`task_id`, unless the caller chose one). The row a failed publish keeps
holds it in its options, so the sweep's copy goes out under the same id as the
message the failed publish may have written; a message delivered again after a
worker was lost (`task_acks_late`) and every `retry()` keep it too. A task can
therefore tell another copy of its dispatch from new work by its own id,
and an effect keyed on it happens once however many copies run.
`tenant.celery.dispatch_key(namespace, *parts)` makes that key: the task id
and what tells the task's effects apart (a recipient, a subject), or `None`
outside a task.

- **E-mail**: every send goes through `send_email`, whose delivery id is
  derived from the task id (`emails.log.delivery_id`); a second copy finds
  the delivery sent, and copies that run at the same moment send under an
  advisory lock on it (`docs/emails/delivery.md`).

A task whose effect is a state of a row needs no id: it claims the state (a
compare-and-set, a unique row), which also covers two dispatches of it from
different paths. A dispatch made inside a task (a chain's next link) is a
dispatch of its own, with an id of its own.

- **A product alert** (`product.tasks._notify_alerts`) is claimed before its
  mail: `is_active` off and `notified_at` set by an update on the row still
  armed, so a second run (another stock or price update) skips every alert the
  first one took. An alert claimed to find its type switched off is armed again.
- **A gift card's delivery** (`giftcard.tasks.deliver_gift_card_email`), queued
  by the purchase, the admin's resend and the daily sweep, reads
  `delivered_at` and sends under an advisory lock on the card
  (`core.db.locks.held_across_calls`). A claim set before the send would lose
  the card's mail with a worker that dies mid-send; the lock goes with that
  worker's session, and the run after it sends the card.

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
| `agent.tasks.deliver_agent_order_event` | `deliver_due_agent_order_events` attempts every `AgentOrderEvent` still `pending` whose next attempt is due (a fan-out beat entry every minute, `docs/agent-order-events.md`) |

A task that has no such state (a transactional e-mail, a live notification) is
covered by the table for a publish that fails; a process killed in that
instant is the one gap, and it is the same one a database commit followed by a
crash has anywhere without a transactional outbox.
