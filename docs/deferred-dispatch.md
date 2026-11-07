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

The sweep publishes outside any transaction, because a broker that stops
answering can hold a publish for longer than
`idle_in_transaction_session_timeout`, and losing the transaction would send every
row already published a second time. It claims its rows first, moving
`next_attempt_at` on by `DEFERRED_DISPATCH_LEASE_SECONDS`, so two overlapping runs
never take the same row and a run killed half way sends at most one row twice.

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
`schema_name` the caller captured), the myDATA chain in `order/tasks.py`, the
data export and account erasure, comment-liked notifications, search
reindexing, the cache and edge-cache purges, the offer refresh, the
recommendation impression, the carrier arrival notification and the admin's
voucher / shipment reissue. A hook that runs inside another `on_commit` hook
(the loyalty tier receivers, `_flush` in `core/cache/invalidation.py`) is
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
