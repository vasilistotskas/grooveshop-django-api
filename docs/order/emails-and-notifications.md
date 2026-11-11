# Order system: emails and live notifications

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 6. Email + WS notification graph

### 6.1 Email triggers (final state, post PR #8)

| Event | Email task | Trigger | Idempotency flag |
|---|---|---|---|
| Order created (offline) | `send_order_confirmation_email` | `order_created` signal | `confirmation_email_sent` |
| Order created (online, pending payment) | — | (deferred to payment success) | n/a |
| Payment succeeded (online) | `send_order_confirmation_email` | webhook handler | `confirmation_email_sent` |
| Payment failed | `send_payment_failed_email` | webhook handler | `payment_failed_email_sent` |
| Status changed → DELIVERED / CANCELED / COMPLETED / REFUNDED / RETURNED | `send_order_status_update_email` | `order_status_changed` signal | `status_update_email_sent_<status>` |
| Status changed → PENDING / PROCESSING | — | (internal milestones — never a customer email) | n/a |
| Order genuinely SHIPPED (status SHIPPED **and** tracking present) | `send_shipping_notification_email` | `order_status_changed`→SHIPPED **or** `order_shipment_dispatched`, whichever completes both conditions | `shipping_notification_email_sent` |
| Refund (in-app, staff action or webhook): one per refund | `send_refund_confirmation_email(refund_id)` | `order_refunded` signal (PR #8), queued on commit once per REFUND ledger row | a `RefundNotification` row per refund, inserted to claim the email before it is sent ([§7.2](refunds.md), [§7.3](payment-ledger.md)) |
| Retail receipt registered with myDATA | `send_invoice_email` | from `send_invoice_to_mydata`, once the MARK is persisted ([§7.4](fiscal-mydata.md)) | `invoice_email_sent` |
| Retail credit document registered with myDATA | `send_credit_email(credit_id)` | from `send_credit_to_mydata` ([§7.4](fiscal-mydata.md)) | `credit_email_sent_<credit id>` in `Order.metadata` |
| Dispute opened (staff) | `send_dispute_notification_email` | `charge.dispute.created` webhook | n/a (rare) |
| Dispute lost (staff) | `send_dispute_notification_email(lost=True)` | `charge.dispute.closed` webhook, once per CHARGEBACK row ([§4.2.1](payments.md)) | the ledger row (a repeat records nothing) |

Every customer email is sent through `emails.send.send_email` and carries
the transactional `List-ID` and `List-Unsubscribe: mailto:` headers
(`emails.headers.build_headers`); store-staff mail carries no list headers.

**Each order email is an `EmailType`, declared in `src/order/emails.py`.**
`order.received`, `order.payment_confirmed`, `order.payment_failed`,
`order.shipped`, `order.delivered`, `order.completed`, `order.canceled`,
`order.returned`, `order.status_refunded`, `order.refunded`,
`order.receipt_issued`, `order.credit_issued`,
`shipping.acs_out_for_delivery`, `shipping.boxnow_at_locker` and the
store-facing `team.new_order`, `team.dispute_opened`, `team.dispute_lost`.
A type holds the template pair, the subject (a msgid with plain named
fields, e.g. `Order #{order_id} Status Update - {status_display}`), the
`build_context(obj, env)` that reads the order for it, a `sample(env)` (an
unsaved object graph the preview renders), the recipient resolver, the
gates and whether the store may switch it off (`delivered`, `completed` and
`shipping.acs_out_for_delivery` can be; the rest are required). The task
calls `send_email(type_key, obj)`; choosing *when* to send (the idempotency
claims above) and the bookkeeping after a send stay in the task. A task
that returned `True`/`False` still does: it maps the `Delivery` explicitly
(`emails.send.was_sent`). A mail the pipeline did not send because the store
switched it off or a gate is closed is a deliberate no-op: the task returns
`True`, keeps its reservation and writes no "email sent" note, except the
store-staff tasks, which return `False` as they did for a store without a
contact address.

`order.delivered`, `order.completed` and `order.review_request` carry a
`review_links` list: one "Write a review" link per distinct product still
to review (`order.emails._review_links`),
to the product page with the `#write-review` fragment that opens the
storefront's review form. It is empty when the store's `product_reviews`
feature is off, or when `PRODUCT_REVIEWS_PURCHASE_REQUIRED` is on and the
order has no account (a guest order is never a verified purchase). The
link and the review gate agree: a verified purchase
(`product.managers.review.purchase_items`) is a COMPLETED order, or a
DELIVERED one whose pay way is collected on delivery (the shopper paid the
courier or carrier; the order stays DELIVERED until the payout is
reconciled). An online-paid order is COMPLETED milliseconds after the
carrier reports it delivered, before this mail is sent.

**The review reminder** (`order.review_request`, `src/order/review_request.py`,
`order.tasks.send_review_request_emails`) is the second invitation, sent
once, `REVIEW_REQUEST_DELAY_DAYS` days (store setting, default `0`, off: a
store chooses to send it,
validated by `validate_review_request_delay_days_setting`, at most a year)
after the order's first move to DELIVERED. `Order` has no delivery
column, so the time is the `OrderHistory` STATUS row of that move
(`status_updated_at` moves again when a cash-on-delivery order completes
days later); an order created already delivered has no such row and is
never reminded. The beat entry `send-review-request-emails` (daily, per
store, `tenant.tasks.fanout_send_review_request_emails`) takes the orders
still DELIVERED or COMPLETED whose delivery fell between the delay and
the delay plus `REVIEW_REQUEST_CATCH_UP_DAYS` (so a store that switches it
on does not mail its whole history, and a missed run costs nothing).
Each order is claimed by stamping `Order.review_request_sent_at` where it
is empty, then mailed: the stamp stays when the order is settled without
a mail (nothing left to review, the customer opted out, the store
switched the type off, reviews are off) or after a failure no retry can
fix, and is released after a transient failure so the next run retries.
`order.emails.unreviewed_products` is the one definition of what is
left to ask for (the delivered and completed mails use it too): a product
the customer has a review of, in any status, is not asked again. The
type is `Kind.MARKETING`: a request for a review is not part of the
order, so `EmailOptOut` applies and the mail carries the unsubscribe
headers and footer; the store can also switch it off.

`order.status_refunded` is the email the REFUNDED status change sends
through `send_order_status_update_email`; `order.refunded` is the one per
refund (`send_refund_confirmation_email`). They share `order_refunded`. The
status template they and `order_returned` build on lives at
`emails/base/order_status` and is no email of its own: no status reaches
a generic fallback (every status that emails has its own type and
template; PENDING, PROCESSING and SHIPPED return before building one).

The admin email preview (`src/emails/preview.py`) renders the
type through `emails.render.render`, the function the task's `send_email`
calls: for a real order through `order.emails.preview_object`, which finds
the refund, receipt, dispute or shipment the email is about, so what it
shows is what that order is sent, subject included
(`tests/integration/emails/test_preview_matches_send.py` sends each
email and compares). An order that has no record the email is about (no
refund, no receipt, no shipment) is reported instead of rendering something
else. Without an order it renders the type's `sample`. A subject lives on
its type and nowhere else.

A receipt or credit mail carries its PDF as an `emails.transport.Attachment`
passed to `send_email`; the bytes are never logged.

Every other domain's emails follow the same pattern (`src/product/emails.py`,
`src/giftcard/emails.py`, `src/b2b/emails.py`, `src/contact/emails.py`,
`src/user/emails.py`,
`src/cart/emails.py`; see `docs/emails.md`). A builder states the language of
every translated row it prints (`set_current_language(env.language)`), so a
product, topic or cart line is named in the recipient's language whatever
language the task loaded it in.

The builders read the order's lines (`_lines`) **inside**
`translation.override`: parler fixes a model's language when the instance
is created, so lines a task prefetched before the override named every
product in the worker's language. Do not prefetch `items` in the task.

**The "your order has shipped" email is honest about timing.** It only
goes out once the order is *genuinely in transit* — `status == SHIPPED`
**and** a tracking number is present — never at voucher-mint. The
courier voucher mints during checkout (COD) or payment-success (online),
which sets the tracking number while the order is still PROCESSING; the
parcel only becomes SHIPPED later, when the carrier reports it moving
(ACS poll / BoxNow webhook). `send_shipping_notification_email` is
therefore dispatched from **two** events and self-gates on both
conditions: `order_shipment_dispatched` (tracking lands) and the
`order_status_changed`→SHIPPED transition. Whichever fires first finds
the other condition unmet and returns a no-op `True` *without* reserving
the `shipping_notification_email_sent` flag; the second one sends. This
also covers the admin who attaches tracking after flipping to SHIPPED
(or vice-versa). PROCESSING is never a customer email/toast — the
order-received (offline) / payment-confirmed (online) notification
already says "we're preparing your order". This is why placing a COD
order used to send three emails at once (received + "processing" +
premature "shipped"); now it sends only the confirmation.

### 6.2 Locale handling

- `Order.language_code` captured at create time
  (`OrderService._seed_language_code` stores
  `core.utils.i18n.active_language_code()` — the request language
  `src/core/middleware/locale.py: RequestLanguageMiddleware` activates from
  the storefront's `X-Language`), so an order's emails and invoice come
  in the language the customer shopped in.
- Whoever stated no language gets **the store's**, never the platform's:
  `core.utils.i18n.store_language()` is `Tenant.default_locale` of the
  bound store (`LANGUAGE_CODE` only with no store bound).
  `get_user_language` / `get_order_language` fall back to it, and a guest
  product alert remembers the language it was made in
  (`ProductAlert.language_code`).
- Merchant-facing mail (`send_admin_new_order_email`, the dispute,
  low-stock, stale-ACS and B2B-application alerts, the review alert)
  renders, subject included, under `translation.override(store_language())`:
  the worker's active language says nothing about the store. The three
  order-domain ones (`team.new_order`, `team.dispute_opened`,
  `team.dispute_lost`) get their recipients and that language from
  `emails.recipients.store_operators()`: the store's contact and owner
  addresses, one mail each, and **no `settings.ADMINS` fallback**. A store
  with neither address sends nothing (`SKIPPED_NO_RECIPIENT`) and the task
  returns `False`.
- There is no pending-order reminder email. An unpaid online order is
  cancelled by `auto_cancel_stuck_pending_orders` (every 15 minutes), so a
  "complete your order" mail a day later reached an order that was already
  gone; `check_pending_orders`, its template and `PENDING_ORDER_REMINDER_*`
  were removed (`Order.reminder_count` / `last_reminder_sent_at` leave the
  model in `order` 0084 and the columns are dropped by `order` 0090).
- A refused create answers 400 with a stable `error.type`
  (`src/order/enum/create_error.py: OrderCreateErrorType`); `detail` and
  `cart` are display text in the request's language, never a contract.
  A cart that fails `validate_cart_for_checkout` is `insufficient_stock`
  or `cart_invalid` (`CartNotReadyError`), and a hold that lapsed between
  the cart check and its conversion is `reservation_unavailable`.
- Every order-email builder (`src/order/emails.py`) runs under
  `translation.override(language)`, and `emails.render.render` renders the
  subject and both bodies under the same override. The language is the
  recipient's, stated by the resolver (`customer_of` uses
  `get_order_language`, `store_operators` the store's language); a task
  passes none.

### 6.3 Customer-notification suppression on chained transitions (PR #7)

Three call paths chain `update_order_status` calls back-to-back.
Without suppression the customer would see two near-identical
emails / toasts within ms:

1. Carrier `_apply_order_status_transition` → DELIVERED →
   `maybe_advance_to_completed(silent_for_customer=True)` → COMPLETED.
2. `handle_payment_succeeded` → PENDING → PROCESSING (then
   `send_order_confirmation_email` covers the same ground).
3. `OrderService.add_tracking_info` admin path → PENDING → PROCESSING →
   SHIPPED.

`OrderService._suppress_customer_status_notifications(order, status)`
pre-stamps two metadata flags:
- `status_update_email_sent_<status>` — short-circuits the email task's
  `_reserve_status_update_email`.
- `suppress_status_ws_<status>` — read by `handle_order_status_changed` to skip
  `notify_order_status_changed_live.delay`.

Internal state still flows: signal fires, `OrderHistory` rows logged, post-save
handler runs. Only user-visible dispatches are skipped.

> Since `handle_order_status_changed` now categorically skips the
> customer email/toast for PENDING and PROCESSING (they're internal
> milestones), the PROCESSING suppression in paths 2 and 3 above is
> belt-and-suspenders. The **COMPLETED** suppression in path 1 is still
> load-bearing — COMPLETED *does* normally notify, so the
> DELIVERED→COMPLETED auto-advance must suppress it to avoid a duplicate
> right after the DELIVERED notification.

### 6.4 Live notifications (WebSocket toasts)

- `notify_order_created_live` — every order create.
- `notify_payment_confirmed_live` / `notify_payment_failed_live` — payment
  webhooks.
- `notify_order_status_changed_live` — meaningful status transitions only.
  Policy lives in `_ORDER_STATUS_COPY` (`src/order/notifications.py`): SHIPPED,
  DELIVERED, COMPLETED, CANCELED. **PENDING and PROCESSING are intentionally
  absent** — they're covered by the order-created / payment-confirmed toasts,
  so surfacing them again would be redundant.
- `notify_order_refunded_live` — refund signal.

There is no standalone "tracking available" toast: it fired at
voucher-mint (the same premature moment as the old shipped email) and
was redundant with the SHIPPED toast, so it was removed alongside the
email fix.

All WS notifications go through `notification.consumers.NotificationConsumer`
and require auth; **guest orders silently get no live notification** (the email
IS sent for guests since it goes to `order.email`).

### 6.5 Marketing email and the unsubscribe link

Marketing mail is the mail a customer did not ask for in the moment:
the re-engagement email (`core.tasks.send_inactive_user_notifications`)
and the abandoned-cart reminder
(`order.tasks.send_checkout_abandonment_emails`). Neither belongs to a
subscription topic, so there is no `UserSubscription` row for the
unsubscribe link to end. The customer's refusal is therefore kept on the
**account**: `UserAccount.marketing_opted_out_at` (nullable timestamp,
additive migration; the moment is the proof of when they asked), beside the
`EmailOptOut` row that is the gate.

- The unsubscribe link (`emails.optout`: the `List-Unsubscribe` URL of both
  emails is the endpoint, `POST /api/v1/emails/unsubscribe/<token>`, RFC 8058
  `List-Unsubscribe=One-Click`, no cookies) writes the `EmailOptOut` row.
  The re-engagement body links to the storefront confirm page
  (`optout.confirm_url`), whose button POSTs the same token; a `GET` on the
  endpoint only redirects there, because a link scanner must not opt anyone
  out. The page's second button, "stop all marketing emails", POSTs
  `/api/v1/emails/unsubscribe/<token>/all`: `EmailOptOut("*")` plus, for an
  account, `marketing_opted_out_at` and the end of its active topic
  subscriptions. `marketing_opted_out_at` is written only by
  `user.services.subscription.opt_out_of_marketing` (that route and the
  customer admin).
- **`EmailOptOut` is the one gate.** Every marketing send is a
  `Kind.MARKETING` email type (`emails.declare.marketing_email`), and
  `send_email` skips a recipient with an `EmailOptOut` row for the type or
  for `*` (every marketing type). No task filters on
  `marketing_opted_out_at`; the field is account state only.
  `emails/0002_import_marketing_opt_outs` gave every account that opted out
  before #368 (flag, no row) its `*` row. The pipeline also gives the type
  the RFC 8058 `List-Unsubscribe` headers (Gmail and Yahoo require
  one-click unsubscribe on marketing mail; the HTTPS URI carries a signed
  token, and the relay DKIM-signs both headers).
- A cart reminder skipped (opt-out, or the type OFF) has its reservations
  settled (`abandonment_notified`), so the next run does not find it again
  and a lifted opt-out never sends a months-old reminder.
- Product alerts (restock, price drop) are marketing types too
  (`product.restock_alert`, `product.price_drop_alert`): each fires once for
  one product the customer picked, and they honour the same opt-out. An
  alert skipped because the type is OFF stays armed; one skipped for an
  opt-out is settled.
- Nothing lifts the opt-out by itself, and staff never do: only the
  customer can, through their own preferences
  (`PATCH /api/v1/user/email-preferences`, `set_marketing_preferences`: a
  `*` row is split into rows for the other types, and the flag clears when
  no row is left; `docs/emails.md`). A restock or price-drop alert made by a
  signed-in customer who opted out answers `409 email_opted_out`. A
  guest's is a double opt-in: every guest request answers the same `202`,
  and the alert starts only when the link emailed to the address is
  confirmed, which also lifts that address's opt-out of the alert's type
  (`src/product/alerts.py`, `docs/emails.md`). Staff may record an
  objection a customer made by phone or email (Loyalty & Engagement tab:
  saving the field writes `EmailOptOut("*")`); once set the field is
  read-only.

### 6.6 The store's copy of a contact or feedback submission

`contact.tasks.send_contact_notification_email_task` and
`send_feedback_notification_email_task` send the `team.contact_message`
and `team.feedback` types (`src/contact/emails.py`) to the store's operators
(`emails.recipients.store_operators`: the contact address, then the
owner's), rendered from `emails/contact/{contact,feedback}_notification.
{html,txt}` in the **store's** language, subject included. The submitter's
address is `Reply-To` (the type's `reply_to`), so replying answers the
customer; it is checked with `validate_email` first, and anything that is
not one bare address (a line break, a display name, two addresses) is
dropped and the mail goes out without `Reply-To`. Files are never attached:
the mail only says they exist. A task returns `False` when nothing was sent
(no operator address, the type switched off, the feedback feature closed).

### 6.7 Account emails (allauth)

`user.adapter.UserAccountAdapter.send_mail` replaces allauth's own
`send_mail`: each of the 19 mails allauth can send (`account/email/*`,
`mfa/email/*`, `socialaccount/email/*`) is an email type
(`user.emails.ALLAUTH_TYPES`) sent by `send_email` from a task, so
allauth's `current_site` is not in the context and must not be
(`SITE_ID` is one store's `Site` row: every store would sign as it).
The templates under `src/core/templates/emails/{account,mfa,social}/` read
the store from `build_email_context` instead: `SITE_NAME`, `SITE_URL`,
the logo and theme. See `docs/emails.md` (allauth mail).
`tests/integration/user/test_allauth_email_store_identity.py` discovers
the kinds from the installed allauth, so a template a later allauth adds
fails there until it is covered.
