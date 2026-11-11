# Order system: common task playbook

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 9. Common task playbook

### 9.1 Adding a new order email

1. Create the `.html` + `.txt` pair under `src/core/templates/emails/order/`.
   `Registry.freeze` refuses a template in that directory no type names.
2. In `src/order/emails.py` add its `build_context(obj, env)` (runs under
   `_email_language`, returns only the keys the templates read plus the
   subject's named fields, never calls `build_email_context`), its
   `sample(env)` and a `_customer_email(...)` / `_staff_email(...)`
   declaration with the subject msgid, the kind of requirement
   (`required`), the gates and the `list_id`. Run `makemessages` and
   translate the new msgids (title, description, subject).
3. Add a Celery task in `src/order/tasks.py` mirroring
   `send_payment_failed_email`:
   - Idempotency reservation helper (`_reserve_<task>_email`).
   - `send_email("<type key>", obj)`, mapping the returned `Delivery` list
     with `was_sent`: no subject, context, recipient, headers or
     `translation.override` of its own.
   - Catch exceptions; on permanent failure, release the reservation.
4. Wire from a signal handler in `src/order/signals/handlers.py` via
   `transaction.on_commit`.
5. Add a case in `tests/integration/emails/test_golden_render.py` (and its
   golden files) and one in
   `tests/integration/emails/test_preview_matches_send.py`; a
   regression test in `tests/integration/order/test_signals.py`.

### 9.2 Adding a new order status

1. Add the value to `OrderStatus` in `src/order/enum/status.py`.
2. Update the transition table in `OrderService.update_order_status`. A status
   that has to do more than change the label (as CANCELED does) gets its own
   service method instead of a row.
3. Add a Greek translation in `src/locale/el/LC_MESSAGES/django.po`, run
   `compilemessages`.
4. Add the matching `_ORDER_STATUS_COPY` entry in `src/order/notifications.py`
   for the WS toast.
5. Create email templates under
   `src/core/templates/emails/order/order_<status>.{html,txt}`.
6. Add a state-machine test in `tests/integration/order/test_state_machine.py`.

### 9.3 Surfacing a new field on the order detail API

1. Add the field on `OrderSerializer` (list shape) or `OrderDetailSerializer`
   (detail-only) in `src/order/serializers/order.py`. Use `extend_schema_field`
   so spectacular emits it.
2. `uv run python manage.py spectacular --color --file schema.yml`.
3. In Nuxt: `pnpm generate:schema && pnpm openapi-ts && pnpm sync:schema`.
4. Type-safe consumption in `app/pages/account/orders/[id].vue`.

### 9.4 Reconciling state after a manual prod fix

If you ever touch order metadata or status directly via shell
(`order.save(update_fields=[...])`), you've bypassed signals.
Recover by:
1. Note exactly which fields you changed.
2. If status was changed: roll it back to `PENDING` and call
   `OrderService.cancel_order(...)` so the canonical path fires.
3. For voucher mint mishaps, follow the order-47 recovery script
   in `commit 05208050`.

### 9.5 A customer email carries the wrong subject or wording

Before touching a template, check the DB translation overlay. Every
non-empty `core.Translation` row is written over the compiled `.po` in
every pod and worker (`src/core/rosetta_storage.py:apply_db_overlay`), so a
stale row wins over a correct catalogue with nothing on disk to show
for it — order #281 (2026-09-12) got "Η Παραγγελία Παραδόθηκε" as its
*order received* subject from an April import row. A fresh
`manage.py shell` never applies the overlay, so it will insist the
catalogue is fine.

1. The rendered subject is in the worker log line
   `Order confirmation email sent for order #N (<template>): '<subject>'`
   and in the order's history note — confirm what was actually sent.
2. `manage.py translation_overlay_audit -l el` lists every row that
   overrides the `.po` (`custom`) with both values side by side.
3. Drop the stale ones — `--drop-ids …` for a handful, `--drop-before
   YYYY-MM-DD` for a bulk import — and `--prune` the rows that equal
   the `.po`. Every write bumps the version key; no restart needed.

## 10. Audit history (PRs #1–#8)

All landed 2026-04-30 → 2026-05-01.

| PR | Theme | Scope |
|---|---|---|
| 1 | Race conditions | COD voucher-mint advance, payment handler row locks, on_commit regression test |
| 2 | State machine completion | Cancel cascade to courier, refund.created webhook, COD reconcile flips payment_status, DELIVERED→COMPLETED auto |
| 3 | UX polish | PaymentStatus translations + display, COD alert suppression, cancellation surface, language_code capture |
| 4 | Email gaps | Shipping notification email wiring, List-Unsubscribe transactional headers |
| 5 | Audit trail | HistoricalRecords on shipments, is_online_payment list serializer, dispute_notification templates |
| 6 | Concurrency hardening | Voucher-mint TTL 90→300s, ACS DELIVERED→COMPLETED tests |
| 7 | Notification dedup | Suppress duplicate customer emails + WS toasts on chained transitions |
| 8 | Last gap | Refund confirmation email |

For the *why* behind each item, read the commit message — they're
written specifically to survive a `git log --grep="^fix"` browse
six months from now.
