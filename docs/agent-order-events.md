# Agent order events

Django tells the agent gateway about every change of an order an agent
placed, and keeps each event until the gateway has it. The gateway renders
the order, signs the webhook and posts it to the platform that placed the
order, within Django's call, and its answer is the event's outcome.

Source: `src/agent/models.py` (`AgentOrderEvent`), `src/agent/outbox.py`,
`src/agent/signals.py`, `src/agent/tasks.py`, `src/agent/admin.py`, the
change detection in `src/order/signals/handlers.py`
(`handle_order_post_save`), the settings in `src/settings/integrations.py`
and the beat entries in `src/settings/schedule.py`. The gateway's side is
`internal/server/internalapi.go` in `grooveshop-agent-gateway`. The code wins
over this page.

## Which orders, which events

An order is an agent's when its `OrderAttribution` says so
(`source_type = agent`), the same rule as the gateway's own order read
(`/api/v1/agent/internal/order/{uuid}`). Nothing is recorded while
`AGENT_GATEWAY_INTERNAL_URL` is empty.

| Event | Recorded when | Where |
|---|---|---|
| `created` | the order's attribution row is written, in its creation transaction | `agent.signals.record_agent_order_created` |
| `updated` | a save changes the order's status or payment status, or sets its tracking number and carrier | `order.signals.handlers.handle_order_post_save` |

The row is written by the save's `post_save` receiver, so it commits or
rolls back with the change only when the save runs inside a transaction:
`ATOMIC_REQUESTS` is off and `Order.save` opens none of its own. The call
sites that change these fields do run inside one:

- order creation: `OrderService._create_order_for_verified_intent` (behind
  `create_order_from_cart`) and `OrderService.create_order_from_cart_offline`,
  both `@transaction.atomic`, the attribution row included;
- status: `OrderService.update_order_status`, `_cancel_order_locked` (behind
  `cancel_order`) and `add_tracking_info`, all `@transaction.atomic`;
- payment: `OrderService.handle_payment_succeeded`, `handle_payment_failed`,
  `confirm_offline_payment` and `settle_cod_payment` (`@transaction.atomic`),
  Stripe's `checkout.session.completed` handler and Viva's
  `_process_event_in_tenant` (`with transaction.atomic()`);
- the admin's change form (Django wraps it in a transaction).

A save outside any transaction commits the order first and writes the event
in its own statement right after, so a process killed between the two loses
that event. A change made with `QuerySet.update()` sends no signal and
records no event.

## The row is the webhook's identity

- `id` (a UUIDv7) is the `Webhook-Id` and `occurred_at` the
  `Webhook-Timestamp`. `occurred_at` is set when the row is written: inside
  the change's transaction, before it commits. Both are sent unchanged on every attempt, so a
  platform tells a retry from a new event (Standard Webhooks; UCP's order
  capability, ucp#659).
- The order's state is read when the event is sent, not stored with it: the
  gateway renders the order as it is then (UCP: send the full order entity).

## Delivery

`POST {AGENT_GATEWAY_INTERNAL_URL}/internal/events/order-status` with the
shared secret in `X-Internal-Token` and this body:

| Field | Value |
|---|---|
| `eventId` | the row's id: the `Webhook-Id` |
| `eventType` | `created` or `updated` |
| `occurredAt` | when the row was written (inside the change's transaction, before its commit), ISO 8601 in UTC: the `Webhook-Timestamp` |
| `schemaName` | the store |
| `orderUuid`, `status`, `paymentStatus`, `trackingNumber` | the order now, soft-deleted or not |

The gateway's answer decides the row:

| Answer | Row |
|---|---|
| `2xx` | `delivered` |
| 401, 403, 404 (`agent.outbox.DEPLOYMENT_FAULTS`): a wrong shared secret, one being rotated, or a wrong URL | attempted again on the backoff; an `ERROR` log line, and the sweep run stops |
| another `4xx` but 408 and 429 (`core.retry.is_permanent_http_status`), such as 400 or 422 | `failed` at once |
| anything else, or no connection within `AGENT_ORDER_EVENT_CONNECT_TIMEOUT` (3 s), or no complete status line and headers within connect + `AGENT_ORDER_EVENT_READ_TIMEOUT` (30 s, above the 25 s the gateway bounds its answer by) | attempted again |

The timeouts are a total deadline. requests' read timeout bounds each wait
for the next bytes, not the whole answer (urllib3's `Timeout` documents the
same of its `total`), so the call runs in a daemon thread that is abandoned
when connect + read have passed. The body is never read: the status and
`Retry-After` are the answer. An abandoned thread ends only when the gateway
stops sending or closes the connection (bytes arriving within the read
timeout keep it alive); being a daemon, it dies with the worker child, which
`CELERY_WORKER_MAX_TASKS_PER_CHILD` replaces. A run leaves at most one, since
it stops on the timeout.

A retryable answer with a `Retry-After` (delta-seconds or an HTTP date)
sets the next attempt to what it asks, at least
`AGENT_ORDER_EVENT_RETRY_AFTER_MIN_SECONDS` (30) and at most
`AGENT_ORDER_EVENT_RETRY_AFTER_CAP_SECONDS` (an hour), in place of the
backoff. The gateway sends one when an "Order created" event arrives before
it has stored its order link, which it writes only once the order-creation
response reaches it. The sweep's minute sets the real delay. Every attempt
counts towards the maximum, whatever its answer.

An order deleted for good fails its pending event at once (`last_error`
"order deleted"); a soft-deleted order is still the platform's order, and its
events are delivered.

The first attempt is queued when the change commits (`dispatch_on_commit`).
Every attempt after it is the sweep's: `deliver-due-agent-order-events` runs
every minute, per store and suspended stores included (the platform still
learns what became of an order it placed), and attempts at most
`AGENT_ORDER_EVENT_SWEEP_BATCH` due events. It takes no new event once it has
spent `AGENT_ORDER_EVENT_SWEEP_BUDGET_SECONDS` (25 s), so a run lasts at most
the budget plus one call's timeouts, under the minute between runs, and slow
answers cannot stack runs. A run stops when the gateway cannot be reached or
refuses the call itself (401, 403, 404), since every other event would get
the same answer.

- **One attempt at a time.** An attempt first claims its row by moving
  `next_attempt_at` to when a failure would set it, so a second worker finds
  it not due, and a worker that dies in the call leaves it to be attempted
  then. The claim also writes a token to `claim`, and every write the
  attempt makes after its call is conditional on it: a manual retry clears
  the token, so an attempt it superseded changes nothing in the new cycle.
  An attempt is a database update before the call and one after; no
  transaction or lock is held across the call.
- **In order.** An event waits while an earlier event of its order is still
  `pending`; a `failed` one holds nothing back. The sweep takes due events one
  at a time, so the next event of an order goes out in the same run.
- **The schedule.** At most `AGENT_ORDER_EVENT_MAX_ATTEMPTS` attempts (10).
  After failed attempt *n* the next waits
  `AGENT_ORDER_EVENT_BACKOFF_SECONDS * 2**(n-1)` (600 s), at most
  `AGENT_ORDER_EVENT_BACKOFF_CAP_SECONDS` (a day), varied by
  `AGENT_ORDER_EVENT_BACKOFF_JITTER` (10%) of itself: about 2.8 days from the
  first attempt to the last. The last failure makes the row `failed`, with an
  `ERROR` log line; when it was the gateway itself (unreachable, or 401,
  403, 404) the run still stops there.

## The task the outbox replaced

The release before queued `order.tasks.push_order_event_to_gateway` on
every order change. It stays registered for one release: a message still
queued or on a retry countdown during the deploy, and a `DeferredDispatch`
row naming it, records the change as an `updated` event (with the time it
runs as `occurredAt`). Remove it in the release after this one.

## The admin

`Audit & Logs → Agent Order Events`, for superusers: every row read-only,
with its attempts, next attempt and last error. **Send again now** puts the
selected `failed` or `pending` events back to `pending` with a fresh set of
attempts and queues an attempt for each. The reset is one update per row
that itself refuses a `delivered` row, so an event delivered after the
selection is not sent again, and only the rows it reset are queued. A
`pending` event whose attempt is in flight can be sent twice under the same
`Webhook-Id`, which a platform dedupes.

## Retention

`purge-expired-agent-order-events` (daily, per store, suspended stores
included) deletes `delivered` and `failed` events that finished more than
`AGENT_ORDER_EVENT_RETENTION_DAYS` (30) days ago, through
`core.retention.delete_in_batches`, which reads each batch with the sweep's
filters under row locks and deletes it in the same transaction: an event
sent again from the admin before the read is `pending` and not read, and
one sent again after it waits for the delete and then finds no row to reset. A `pending`
event is never deleted. An
event goes with its order. The staging refresh deletes every row
(`devtools.anonymise.rules`): they are production's to send.
