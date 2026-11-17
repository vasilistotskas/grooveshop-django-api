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

Each event is a row written in the transaction of its change, so it exists
exactly when the change committed. A change made with `QuerySet.update()`
sends no signal and records no event.

## The row is the webhook's identity

- `id` (a UUIDv7) is the `Webhook-Id` and `occurred_at` the
  `Webhook-Timestamp`. Both are sent unchanged on every attempt, so a
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
| `occurredAt` | when the change committed, ISO 8601 in UTC: the `Webhook-Timestamp` |
| `schemaName` | the store |
| `orderUuid`, `status`, `paymentStatus`, `trackingNumber` | the order now |

The gateway's answer decides the row:

| Answer | Row |
|---|---|
| `2xx` | `delivered` |
| `4xx` other than 408 and 429 (`core.retry.is_permanent_http_status`) | `failed` at once |
| anything else, or no answer within `AGENT_GATEWAY_HTTP_TIMEOUT` | attempted again |

The first attempt is queued when the change commits (`dispatch_on_commit`).
Every attempt after it is the sweep's: `deliver-due-agent-order-events` runs
every minute, per store and suspended stores included (the platform still
learns what became of an order it placed), and attempts at most
`AGENT_ORDER_EVENT_SWEEP_BATCH` due events. A run stops when the gateway
cannot be reached, since every other event would wait out the same timeout.

- **One attempt at a time.** An attempt first claims its row by moving
  `next_attempt_at` to when a failure would set it, so a second worker finds
  it not due, and a worker that dies in the call leaves it to be attempted
  then. An attempt is a database update before the call and one after; no
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
  `ERROR` log line.

## The admin

`Audit & Logs → Agent Order Events`, for superusers: every row read-only,
with its attempts, next attempt and last error. **Send again now** puts the
selected `failed` or `pending` events back to `pending` with a fresh set of
attempts and queues an attempt for each; a `delivered` event is not sent
again.

## Retention

`purge-expired-agent-order-events` (daily, per store, suspended stores
included) deletes `delivered` and `failed` events that finished more than
`AGENT_ORDER_EVENT_RETENTION_DAYS` (30) days ago, through
`core.retention.delete_in_batches`. A `pending` event is never deleted. An
event goes with its order. The staging refresh deletes every row
(`devtools.anonymise.rules`): they are production's to send.
