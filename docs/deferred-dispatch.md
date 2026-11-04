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
   not lost. If even the row cannot be written, the whole payload is in the log
   line;
3. `tenant.tasks.redispatch_deferred_tasks` (beat, every minute) publishes the
   rows again and deletes each once its task is out. A row that fails again
   waits `DEFERRED_DISPATCH_BACKOFF_SECONDS * 2**attempts` (capped by
   `DEFERRED_DISPATCH_BACKOFF_CAP_SECONDS`); when the broker is still down the
   run stops after the first row instead of waiting out the timeout for each. A
   row older than `DEFERRED_DISPATCH_RETENTION_DAYS` is deleted with an `ERROR`
   (the work was not done) and its payload in the log.

Any other exception is a bug and still raises. There is no metrics stack on this
platform: the number of `DeferredDispatch` rows is the count of publishes
waiting, and each failure is an `ERROR` log line.

`robust=` is gone from `dispatch_on_commit`: every caller has this behaviour.

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
