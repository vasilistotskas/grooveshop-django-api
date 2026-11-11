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

A status written by hand skips the service that owns the transition.
`order.save()` still stamps `status_updated_at`, settles an unpaid payment
on CANCELED / RETURNED and sends `order_status_changed`, so the customer's
status email and toast can go out; `Order.objects.filter(...).update(...)`
does none of that. Neither checks the transition table
(`OrderService.update_order_status`), and a hand-written CANCELED releases
no stock, refunds nothing, cancels no voucher and records no
cancellation ([§7.1](cancel.md)).

There is no one recipe. Recovery depends on what the order was before the
edit and on what really happened to the parcel, the stock and the money:

1. Note exactly which fields you changed and what they held before.
2. Read what the status does not say: the shipment's state (whether the
   courier has the parcel), the stock the order holds (its `StockLog` rows)
   and the money (`payment_status` and its `PaymentTransaction` rows).
3. Then, by the status the order had BEFORE the edit:
   - **PENDING or PROCESSING, and the order is to be canceled.** Put the
     old status back and call
     `OrderService.cancel_order(order, reason, customer_initiated=False)`.
     It accepts only PENDING and PROCESSING, releases what the order still
     holds, records the cancel, asks the courier afterwards and refunds a
     paid order. This is the only case in which to call it.
   - **PENDING or PROCESSING, and the order goes on.** Put the old status
     back, and the old `payment_status` with it when the edit was a save
     to CANCELED or RETURNED: that save closes an unpaid payment
     ([§2.2](state-machines.md)).
   - **SHIPPED, DELIVERED or COMPLETED.** Never set it to PENDING in order
     to cancel it: the customer has the goods or is about to, and a cancel
     would put their stock back on sale and refund the payment. Put the
     true status back, by hand as the edit was made (the transition table
     has no step backwards). If the goods do come back, the order is
     RETURNED ("Mark selected orders as returned", or
     `update_order_status`), and the money and the stock go back through
     the "Return and refund" and "Restock returned items" actions, each
     decided on the parcel in hand ([§7.2](refunds.md)).
4. For voucher mint mishaps, follow the order-47 recovery script
   in `commit 05208050`.

This section used to tell every case to roll the status back to PENDING
and cancel, which is right for the first case only. `update_order_status`
refuses CANCELED, so `cancel_order` is the one way to cancel.

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
