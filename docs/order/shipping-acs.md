# Order system: ACS Courier

Part of the order reference. [`docs/order/index.md`](index.md) lists every file
and says which file holds each § number used here.

## 5.1 ACS Courier (`src/shipping_acs/`)

- **REST API**, polling-based (no webhooks).
- Voucher mint: 3-phase `claim → API → persist` design in
  `AcsService.create_voucher_for_order`. Survives
  `idle_in_transaction_session_timeout` — see
  `project_acs_voucher_orphan_prevention.md`. **TTL 300s** (PR #6). The
  claim keeps two workers from minting at once; it was never designed
  against a lost answer.
- **A voucher request of unknown outcome is never sent again** (#622).
  ACS's manual gives `ACS_Create_Voucher` no idempotency key and no
  duplicate error, and no call finds a voucher by our reference before it
  is on a pickup list (April 2026 edition, p. 7-9, 21-22); stage order 682
  got two vouchers from two requests. The client raises
  `AcsOutcomeUnknownError` for every failure after the request may have
  left (a read timeout, a connection dropped after the body was sent, which
  `requests` reports as a plain `ConnectionError`, a 5xx, a non-JSON 200);
  only a connection never made (`ConnectTimeout`, `NewConnectionError`,
  `NameResolutionError`), the request cap, a 403/406 and an ACS refusal
  release the claim for a retry. An unknown outcome, an exception the
  client does not classify, a success with neither `Voucher_No` nor
  `Error_Message`, and a claim past its TTL (a worker stopped before the
  save) all set `AcsShipment.voucher_request_unknown_at`: the order is
  noted, `ops.acs_voucher_request_unknown` is sent once, and every caller
  (the task, its retries, a re-dispatch, "Issue ACS voucher now") is
  refused with `AcsVoucherRequestUnknownError` until staff use **Adopt ACS
  voucher** (the number may belong to no other shipment; ACS must hold it,
  except a number recorded on this shipment as our own voucher ACS refused
  to delete, which needs a hand confirmation; and its `ACS_Trackingsummary`
  recipient must equal the `Recipient_Name`
  the lost request sent, with no other waiting order sharing that name;
  anything else, a number known only from a refused pickup list included,
  needs a written hand confirmation, because the manual documents no
  format for that field; no create is sent) or **No voucher at ACS: request
  again** (a note is required;
  queues the mint once). A shipment that no longer needs a voucher (retired,
  or its order canceled or refunded) stays counted but can only be closed
  (**No voucher needed: close**, a note), never adopted onto or minted for. A
  late answer that
  arrives after another worker recorded the outcome as unknown is saved
  and clears the state. Nothing is automatic: no documented call ties a voucher
  to our
  `Reference_Key1` before the pickup list. `docs/acs-debugging.md` has the
  error table.
- **Voucher requests wait while ACS is failing** (#631,
  `src/shipping_acs/voucher_hold.py`). One lost answer costs one staff check;
  an outage that answers 5xx or hangs would cost one for every order
  placed while it lasts. So the unknown outcome of a create this store
  SENT (`AcsService._voucher_request_lost`: not an expired claim, which
  says a worker stopped, and not another alias) begins an
  `AcsVoucherHold`, one row per span in the store's schema, at most one
  in force (a database constraint).
  - **Held.** While it is in force `create_voucher_for_order` sends
    nothing and raises `AcsVoucherRequestHeldError`: the shipment is
    stamped `voucher_request_held_at`, no claim is left to expire, no
    `last_error`, no alert, and never `voucher_request_unknown_at`
    (nothing reached ACS). The task returns
    `{"status": "voucher_request_held"}` and does not use its retries,
    which an outage outlasts.
  - **Where it is decided.** Once before the claim, and again inside the
    request slot right before the POST (`AcsClient._admit_voucher_request`),
    because a request can wait `ACS_SLOT_WAIT_SECONDS` for its slot behind
    the request that fails. So a failing ACS costs the requests in flight
    (`ACS_MAX_CONCURRENT_REQUESTS`), not the queue behind them. A hold
    that cannot be read sends nothing either
    (`AcsVoucherRequestNotSentError`, retried by Celery). A request
    stopped at its slot is not a call to ACS: it leaves no `AcsApiCall`
    row and no call log line, so the history of an outage shows only the
    creates that left.
  - **The stamp.** `voucher_request_held_at` goes only when ACS answers
    the request (a voucher, a refusal), its outcome becomes unknown, or
    the order can no longer be sent: its request cannot be built, or the
    mint refuses it because the shopper owes the money online again
    (`ShipmentAwaitingPaymentError`; the payment that settles it
    dispatches the mint again). A request that was let through and never
    reached ACS (a connection never made, the request cap, 403/406), or
    that something other than ACS answered while a hold is in force (an
    HTTP 4xx outside ACS's envelope, `AcsUndocumentedHttpError`), keeps
    it, so the sweep still sends the order after the task's retries are
    spent. With no hold in force (the hold ended and the sweep is sending
    what waited) a held order's request is an ordinary request again:
    that 4xx, like a refusal, takes it out of the sweep and alerts staff
    once, so nothing is repeated every tick with nobody told. Like
    the claim (#647), the stamp is a request's to settle only while the
    row holds that request's claim: a stale answer (its claim expired,
    staff requested again, and that later request is held) is deleted at
    ACS or refused without touching the stamp, which is the later
    request's; Phase 3 clears it only together with the voucher it saves.
    A held request takes no claim, so no unknown state
    (`metadata["unknown_request"]`) can ever name it.
  - **Asking again.** `issue_held_acs_vouchers` (beat
    `issue-held-acs-vouchers`, every 5 minutes, per store) is the one
    retry. Closed: once `ACS_VOUCHER_HOLD_RETRY_SECONDS` (300) have passed
    since the last lost answer it asks ACS one read,
    `ACS_Get_Pickup_Lists` for today (manual p. 15-16; no voucher needed,
    writes nothing). No answer: nothing else is sent. An answer, or staff
    pressing **ACS answers again: send one request** on the hold (admin:
    ACS voucher holds; change permission; recorded in the admin log),
    allows **one request first**, one at a time: a row lock on the hold
    picks it across workers, and the request that goes takes the turn
    (`AcsVoucherHold.trial_request`: the shipment and that request's
    mint claim) for `client.worst_case_request_seconds` (78 s at the
    default `ACS_HTTP_TIMEOUT`, shorter than the 5-minute tick, so a turn
    a dead worker never gave back is over before the next tick).
  - **Which held order goes first.** The one the sweep tried longest ago:
    never tried before all others, then the one held longest
    (`voucher_request_retried_at`, written by the sweep before it
    dispatches). So an order whose request cannot be sent, whatever the
    reason, is not chosen on every tick while the rest wait behind it.
  - **What ends it, by what each answer proves** (the table in
    `src/shipping_acs/voucher_hold.py` is the rule):

    | Outcome of the request that has the turn | The order | The hold |
    |---|---|---|
    | ACS returns a voucher | saved | **ends**: creating a voucher works. The rest follow on the next tick, spaced by `ACS_POLL_DISPATCH_SPACING_SECONDS` |
    | ACS refuses in its own envelope (`ACSExecution_HasError`, a value-row `Error_Message`) | rejected as before (`ops.shipment_rejected`, "fix the address"), no longer held | stays "one request first", the turn free at once: ACS executed a create, nothing shows one succeeds. The next held order goes next |
    | an HTTP 4xx that is not ACS's envelope (the manual has 200, 403, 406 only: a proxy's 429, a gateway's 404) | a held order **stays held** while a hold is in force (always so for the request that has the turn: the hold closes again), with no alert; with no hold in force, or for an order that was not held, it is rejected as before and staff are told once to send it again (not to fix an address) | **closes again, wait doubled**: it says nothing about ACS, and nothing about the order. Without a hold it begins none |
    | unknown outcome | #626's state, no longer held | closes again, wait doubled (`AcsVoucherHold.retry_after`, capped by `ACS_VOUCHER_HOLD_RETRY_MAX_SECONDS`, 3600) |
    | never reached ACS (a connection never made, 403/406) | still held | stays "one request first", **the turn free at once**: ACS processed nothing, and that is known now |
    | stopped before its turn (the request cap, a hold that could not be read) | still held | unchanged: it never had the turn |

    **Only the request that has the turn settles it.** Another request of
    the store can still be out: it left before the hold began, or its
    turn ended with its lease and the next request took it. Its outcome
    is its own order's and leaves the hold as it is: its voucher does not
    end the hold (it shows nothing newer than the lost answer, the reason
    a voucher changes nothing while the hold is closed), its refusal or
    4xx frees and doubles nothing, and its unknown outcome is recorded as
    the last failure without counting as a failed first request.
  - **Staff see one state.** The dashboard row "ACS voucher requests are
    held" (since when; `in_force=yes` on the hold list) and the shipment
    filter `attention=voucher_held`; there is no email and no order note
    per held order. The row is drawn while the hold is closed or requests
    wait behind it (`attention.voucher_holds_holding_requests`). A hold
    that allowed one request first and has nothing left to send (the
    orders were canceled, or ACS refused them) stays in force, so the
    next order's request goes alone and its voucher ends the hold, but it
    is no row: nobody has anything to do. An order canceled while it
    waits is not sent (`attention.voucher_requests_held`). "Issue ACS
    voucher now" says the request waits when the mint's own rule holds
    it at the press (`voucher_hold.holding`: the hold is closed, or
    another request has the turn); with the turn free the request is the
    one sent, and the page says it was dispatched.
  - **A release reverted while orders are stamped.** The previous release
    does not know the column or the table: to it a stamped shipment is a
    `pending_creation` one with no claim and no unknown flag. Nothing
    sends it by itself (the sweep is gone with the release), it is listed
    as a stranded mint after 24 hours, and "Issue ACS voucher now" sends
    it, once, like any other. Deployed again, the sweep sends what is
    still stamped and has no voucher.

  ```
  no hold ──unknown outcome of a create we sent──► HELD (nothing sent)
     ▲                                               │ wait over + ACS answers a read,
     │                                               │ or staff say it answers again
     │                                               ▼
     └──ACS returns a voucher──── ONE REQUEST FIRST ──► HELD, wait doubled:
                                   │                     unknown outcome, or an HTTP 4xx
                                   │                     that is not ACS's envelope
                                   ├ ACS refuses: the next held order goes next
                                   └ never reached ACS: the same order goes again
  ```
- **Vouchers ACS names that match no shipment.** A refused
  `ACS_Issue_Pickup_List` names every unprinted voucher of the account
  (p. 14). `_issue_pickup_list_unlocked` keeps each one that is no
  shipment's voucher nor a multipart child as an `AcsUnrecognisedVoucher`,
  sends `ops.acs_unrecognised_vouchers` the first time it is named, and the
  task result lists them under `unrecognised`. Staff adopt it from the
  order's shipment, delete it at ACS (`ACS_Delete_Voucher`, only before a
  pickup list; refused while a mint is in flight or an order that still
  needs its voucher waits in the unknown state, since it may be that
  order's voucher; expired claims are turned into that state first) or mark
  it as checked. Both ask for a note and keep it on the row. A duplicate
  the mint deletes at ACS resolves its row. A run during an in-flight mint
  records
  nothing, and a shipment that saves a number resolves its row.
  `_build_create_voucher_params` uses `order.country_id` verbatim as
  `Recipient_Country` (no lookup — `Country.pk` IS the alpha-2 code)
  and refuses with `AcsAPIError` if it is missing, rather than
  defaulting to "GR" — ACS is Greece-only today, but which countries
  it serves comes from active `ShippingRate` rows ([§5.0](shipping.md)), never
  a
  hardcoded fallback here.
- Polling: `poll_shipment_tracking` runs in two phases (read,
  no lock; API call, no transaction; persist with
  `select_for_update`) — survives slow ACS responses without
  losing `last_polled_at`.
- COD: `reconcile_cod_payouts` runs daily, upserts `AcsCodPayout`
  rows from the ACS COD beneficiary endpoint, and flips
  `Order.payment_status` for matched vouchers. **Wire schema**: the
  voucher number arrives in the `POD` column (there is no
  `Voucher_No` in this response); `Customer_RefNo_1/2` echo
  `Reference_Key1/2` from voucher mint (`order.id` / `order.uuid`)
  and are the fallback match keys. A row with no readable voucher
  (empty `POD`, no `Customer_RefNo` match) is counted as `skipped` and
  stored all the same, with an empty `voucher_no` and keyed on
  `AcsCodPayout.row_fingerprint` (SHA-256 of the identifying ACS
  columns plus the payment date): the `(voucher_no, cod_payment_date)`
  unique constraint is partial (`voucher_no <> ''`) and a second partial
  unique constraint covers `row_fingerprint <> ''`, so a rerun updates
  the row and two different unreadable rows of one date both survive.
  Rows ACS reported for a voucher we do not know (`unmatched`) and
  unreadable ones alert the store's operators. Staff clear such a row
  from the payout list with "Mark as reconciled" (a dialog asking for a
  note; stamps `reconciled_at` / `reconciled_by` /
  `reconciliation_note`, never edits the payout; service
  `AcsService.reconcile_cod_payout`). Backfill missed dates with
  `manage.py reconcile_acs_cod --days N [--silent]` (`--silent`
  suppresses customer COMPLETED notifications — mandatory for
  backfills).
- **COD received outside ACS**: the nightly `alert_unremitted_cod_payouts`
  treats a delivered COD parcel as unremitted unless an `AcsCodPayout`
  exists for its voucher (ACS's own statement). When the merchant was
  paid by another route and ACS never reports it (prod order 73), an
  operator uses the **Record COD received outside ACS** detail action on
  the `AcsShipment` admin (a note is required). It calls
  `AcsService.record_cod_received_outside_acs`, which refuses unless the
  shipment is a DELIVERED COD with an amount due, has no `AcsCodPayout`
  and is not already recorded; it stamps `cod_received_outside_acs_at/_by/
  _note` (audited by simple-history) under the shipment row lock and marks
  the order paid through `_mark_cod_order_paid_if_pending` (silent, a
  no-op if already paid). `_unremitted_cod_rows` then skips the parcel. No
  `AcsCodPayout` is ever created for it.
- HTTP 403/406 (`AcsAuthError`) is **retryable**; only a persistent
  rejection means a bad key/IP. ACS answers 406 to overlapping calls
  from one account. 2026-10-20 prod saw 22% of tracking calls rejected
  (2% in July), every one inside the `poll_acs_tracking_batch` fan-out:
  each poll makes two calls of 0.6 to 4 s, so six to eight were in
  flight across the worker pods (celery-worker went from 2 to 3
  replicas around 10-19; rejections went from 7-31 a day to 106, then
  224). Staggering task *start* times does not bound that, so
  `AcsClient._call` (every alias) takes one of
  `ACS_MAX_CONCURRENT_REQUESTS` slots per ACS `Company_ID` and store
  (`src/shipping_acs/concurrency.py`).
  - **Why 2.** ACS publishes no concurrency or rate figure; the number
    is ours. Measured 2026-10-21 over 10 days of production logs
    (10-11 to 10-20, VictoriaLogs): for every tracking task, how many
    ACS tasks were in flight when it started, against whether ACS
    rejected it.

    | In flight | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9+ |
    |---|---|---|---|---|---|---|---|---|---|
    | Rejected | 0.9% | 2.0% | 3.6% | 4.0% | 6.9% | 7.9% | 11% | 17% | 19-50% |

    2 halves the rate against 3 and still serves the batch (below).
    Re-measure the same way before changing it; there is no
    zero-rejection setting, 1 in flight still saw 0.9%.
  - **Reserved slot.** The poll batch is paced to keep its slots busy, so
    without a reserve a shopper's `price_calculation` or
    `address_validation` could wait the full `ACS_SLOT_WAIT_SECONDS`
    behind it and then fall back or 502. The last
    `ACS_INTERACTIVE_RESERVED_SLOTS` (default 1) slots are for interactive
    calls only: the tracking polls (`tracking_summary` /
    `tracking_details`, `background=True` in `AcsClient._call`) may take
    the other `ACS_MAX_CONCURRENT_REQUESTS - reserved` slots, every other
    alias may take any slot and tries the reserve first. One pool with a
    priority split, not a second pool. At 2 slots the polls run one at a
    time, the measured 0.9% level, and one slot stays free for shoppers.
    A setting that leaves the polls no slot refuses to start.
  - **Dispatch spacing.** Mean poll task duration was about 2 s
    (`ACS_POLL_AVERAGE_TASK_SECONDS`), so one poll slot serves one poll
    every 2 s. `ACS_POLL_DISPATCH_SPACING_SECONDS` is derived as
    duration / poll slots (2 s), so the 200-shipment cap finishes about
    400 s after the tick: inside the 15-minute cutoff (a shipment polled
    in the last 15 minutes is not a candidate) and the 13-minute batch
    lock (which is held only while the batch enqueues the polls, so it
    never competes with them). Polls do not queue past
    `ACS_SLOT_WAIT_SECONDS`.
  - **Slots.** redis-py `Lock` keys (`CustomCache.lock`, tenant-keyed),
    non-blocking, with a per-holder token: release deletes the key only
    while the token is still the holder's, so a lease that ran out and
    was taken by another worker is never deleted from under it. The
    lease is `client.worst_case_request_seconds` (every connect attempt
    of `_RETRY_CONFIG` at `ACS_HTTP_TIMEOUT`, urllib3's backoff, then
    one read: 78 s at the 15 s default), derived, not configured. A
    killed worker frees its slot when the lease ends.
  - **Full cap.** A call waits up to `ACS_SLOT_WAIT_SECONDS`, then raises
    `AcsRetryableError` for Celery to retry. With Redis unreachable the
    cap is skipped and logged: a rejected call was never processed, and
    `src/core/caches.py` sets the policy that an unreachable Redis costs
    speed, not requests.
  - **Retries.** `poll_acs_tracking_one` retries at 10, 20, 40, 80, 160 s
    with no jitter (Celery's jitter draws from zero, which re-entered the
    congestion at once).
- A refused or uncollected COD parcel: ACS records `attempted` (order →
  SHIPPED), texts the customer and holds the parcel about ten days, then
  reports `returned_flag=1` → shipment RETURNED → order RETURNED, and the
  save settles the uncollected payment to CANCELED. Stock is not touched
  by that transition — the goods arrive back days later and may be
  damaged, so restocking is a staff decision on the physical parcel.
- Staleness watch: `check_stale_acs_shipments` (daily 09:00 Athens)
  emails ADMINS about non-terminal shipments with no tracking event
  for `ACS_STALE_SHIPMENT_DAYS` (default 3) days. Dedup via the
  `stale_alert_sent` claim flag, re-armed by the poller when a new
  event arrives. Dead vouchers are retired by a human via the admin
  action "Retire selected shipments" (local CANCELED — stops the
  poller; does not call `ACS_Delete_Voucher`).
- Manifest pre-flight: ACS refuses the whole pickup list while ANY
  voucher on it is unprinted, so "print everything outstanding" is the
  operation that unblocks it. The shipment changelist action "Print
  labels for selected shipments" returns ONE merged PDF (ACS has no
  bulk print alias — `ACS_Print_Voucher` takes a single `Voucher_No`,
  so `AcsService.fetch_labels_pdf` concatenates them); pair it with the
  `Label printed at` / empty filter. A voucher that fails is reported
  and the rest still print. Downloading stamps `label_printed_at`
  through the same `fetch_label_bytes` choke point as the per-order
  button.
- An "empty day" is not a refusal. `ACS_Issue_Pickup_List` takes a date
  and no voucher list — ACS picks the vouchers, scoped by
  `Pickup_Date` — so when every local candidate is a dead voucher (no
  tracking event, older than `ACS_STALE_SHIPMENT_DAYS`, and not the
  product of a re-mint) the service logs them at WARNING and returns
  `None` instead of raising. One live candidate still makes a refusal a
  failure. Those dead rows are reported by `check_stale_acs_shipments`,
  which owns them; this path deliberately does not send a second alert.
- An unprinted-labels refusal (`Unprinted_Found > 0`) is a status, not
  a failed task. The service raises `AcsUnprintedVouchersError`; the
  daily task returns `{"status": "blocked_unprinted", "unprinted": [...],
  "order_ids": [...]}` and notes each blocked order. Nobody is emailed,
  and there is no 15:45 warning any more: on webside the labels are
  printed the next morning, so the 16:30 run is refused most days, and
  ACS scans a printed parcel as collected the same day with or without a
  manifest (2026-09-21 to 10-10: 43 of 45 parcels collected with no
  pickup list, Saturdays included, when no list runs at all). The mails
  were daily noise the merchant asked to stop. To issue a list anyway:
  "Print labels for selected shipments", then "Issue ACS pickup list
  now". Every other refusal is a plain `AcsAPIError` and still fails the
  task.
- Debugging ACS (call history, pickup-list run records, the shipment
  timeline, LogsQL queries): `docs/acs-debugging.md`.
- The **silent** form of the same refusal is classified as the same
  `blocked_unprinted`. Once a voucher stays unprinted into a later day ACS
  stops naming it and answers `PickupList_No: null`, `Unprinted_Found: 0`,
  `Error_Message: null`, `Unprinted_Vouchers: []` (voucher 9818420312: named
  2026-10-22, silent 10-23; voucher 9819787972, order #448: named 10-27,
  silent 10-28; `ACS_Get_Pickup_Lists` was empty, so reconciliation adopted
  nothing). When the issue returns no `PickupList_No`, reconciliation
  adopts nothing, the day is not all dead vouchers, and EVERY candidate has
  `label_printed_at` NULL, `issue_daily_pickup_list` logs a WARNING ("did
  not name the unprinted voucher(s)") and raises
  `AcsUnprintedVouchersError` with the local unprinted vouchers in
  `raw["Unprinted_Vouchers"]`, so the task takes the one path above: status,
  order notes, no email. If ANY candidate is locally printed the silence is
  unexplained and stays an ERROR log plus `AcsAPIError`; that case failed
  the infrastructure's production smoke gate when it was misclassified. The
  `warn-unprinted-acs-vouchers` beat entry is retired.
- The stale digest's "stranded mint" class (`pending_creation` > 24 h)
  excludes shipments whose voucher request has an unknown outcome (their
  own alert and counter; its remedy, "Issue ACS voucher now", is what they
  must not get) and orders awaiting their online payment — the auto-cancel owns
  those, and listing them invited an "Issue ACS voucher now" for unpaid
  goods. The exclusion is `awaiting_online_payment_q("order__")`, the
  same rule as `OrderQuerySet.awaiting_online_payment()` and the
  follow-up flag ([§2.3](state-machines.md)): an online order whose payment
  settled in any way,
  a refund included, is no longer "waiting".
- `Recipient_Zipcode` is refused locally when the order's postcode does
  not match its country (`core.validators.address.postcode_matches`):
  a non-retryable `AcsAPIError` naming the field, recorded in
  `metadata["last_error"]` with the mint claim released. A valid one goes
  out with its space removed. The old digits-only filter turned a
  4-letter postcode into `""` and ACS answered with its generic "fill
  data error" (order 316, 2026-09-24).
