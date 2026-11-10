# Debugging ACS

ACS (the courier) is called through one chokepoint, `AcsClient._call`
(`shipping_acs/client.py`). Everything below follows from what that
chokepoint records. The code wins over this page; the observability code is
`shipping_acs/observability.py`.

## What is recorded

**One structured log line per ACS call**, logger `shipping_acs.client`,
message `ACS call <alias> outcome=<outcome> http_status=<n> duration_ms=<n>`.
The queryable fields are top-level keys of the JSON log object
(`core.logging.JsonFormatter` merges `extra={"fields": ...}`):

| Field | Meaning |
|---|---|
| `acs_alias` | the ACS alias (`ACS_Issue_Pickup_List`, `ACS_Print_Voucher`, ...) |
| `outcome` | `ok`, `business_error`, `retryable`, `auth`, `transport`, `unexpected` |
| `duration_ms` | wall time of the call, slot wait included |
| `http_status` | HTTP status, absent when no response arrived |
| `error_message` | ACS's `ACSExecutionErrorMessage`, or the value row's `Error_Message` |
| `voucher_no`, `pickup_date`, `pickup_list_no`, `reference_no`, `order_id` | identifiers present in the call, omitted otherwise |
| `schema` | the store (tenant schema), the formatter's own field |

Levels: INFO for `ok`; WARNING for `business_error` (ACS refused, in the
envelope or in the value row), `auth` (403/406), `retryable` (5xx) and
`transport` (connection error or timeout); ERROR only for `unexpected` (a
non-JSON body, an HTTP 4xx other than 403/406, or an exception that is not an
ACS one). The outcome says how the call failed, not whether ACS executed it:
see "A voucher request with an unknown outcome" below. Credentials (`Company_ID`, `Company_Password`, `User_ID`,
`User_Password`, `AcsApiKey`) never reach the log or the database.

**A persisted call history**, `AcsApiCall` (admin: ACS API Calls, platform
superusers only because the redacted payload still holds the recipient's name,
address and phone): alias, outcome, HTTP status, duration, ACS message, the
redacted and size-capped request parameters and response, and the voucher /
order / pickup date / reference it concerns. PDF and base64 bodies are
replaced by `<N chars omitted>`. Rows older than `ACS_API_CALL_RETENTION_DAYS`
(default 90) are deleted by `purge_expired_acs_api_calls`, fanned out daily
by `tenant.tasks.fanout_purge_expired_acs_api_calls`; a successful tracking
read (`ACS_Trackingsummary`, `ACS_TrackingDetails`, the 15-minute poll) is
kept only `ACS_API_CALL_POLL_RETENTION_DAYS` (default 14).

**A run record per pickup-list attempt**, `AcsPickupListRun` (admin: ACS
Pickup List Runs, visible to store staff): date, start time, outcome
(`issued`, `adopted`, `nothing_to_issue`, `blocked_unprinted`,
`blocked_unprinted_silent`, `error`), the candidate vouchers, the vouchers ACS
named as unprinted, the candidates unprinted locally, ACS's message, the
error, the `AcsPickupList` when one exists and who pressed the button (empty
for the 16:30 beat task). Written by `AcsService.issue_daily_pickup_list` on
every outcome, the admin "Issue ACS pickup list now" action included. A run
skipped because another run holds the single-flight lock writes nothing.

**A shipment timeline**: ACS Shipments, open a shipment, tab Diagnostics,
field Timeline: row created, label printed, every pickup-list run that had the
voucher as a candidate and how it ended, the pickup list it is on, tracking
events and the last ten ACS calls naming the voucher. The order page's ACS
Shipment panel links to it.

All LogsQL below is for VictoriaLogs; add `schema:<store>` to scope a store.
If your pipeline nests the JSON under a `log.` prefix, use `log.acs_alias`.

## Why was no pickup list issued on date X?

1. Admin: ACS Pickup List Runs, filter by pickup date. The outcome says it:
   - `blocked_unprinted`: ACS named unprinted vouchers (`unprinted_vouchers_acs`).
     Print their labels, then "Issue ACS pickup list now".
   - `blocked_unprinted_silent`: ACS named nothing, no list was adopted from
     `ACS_Get_Pickup_Lists`, and every candidate is unprinted locally
     (`unprinted_vouchers_local`). Same fix. Seen when a voucher stays
     unprinted into a later day (2026-10-23, 10-28).
   - `nothing_to_issue`: no candidate, or only dead vouchers
     (`check_stale_acs_shipments` owns those).
   - `error`: read `error_message`. With a locally printed candidate, ACS's
     silence is unexplained and is an ERROR on purpose.
   - no run row at all: the task did not run (check `PeriodicTask.last_run_at`
     for `issue-acs-pickup-list`) or lost the lock.
2. The call itself: ACS API Calls, alias `ACS_Issue_Pickup_List`, that date.
   `response` shows the exact answer (`PickupList_No`, `Unprinted_Found`,
   `Error_Message`).
3. LogsQL: `acs_alias:ACS_Issue_Pickup_List pickup_date:2026-10-28`

## Is voucher V printed and on a list?

ACS Shipments, search the voucher: `Label printed at` is our mirror of a
print made through us (a label printed in ACS's own portal never reaches it),
`Pickup list` is the manifest. The Timeline lists the runs that had it as a
candidate and whether ACS or the local flag called it unprinted. To ask ACS:
ACS API Calls, search the voucher, alias `ACS_Pickup_List_Display_Voucher`.

LogsQL: `voucher_no:7227891111 | sort by (_time)`

## A voucher request with an unknown outcome

`ACS_Create_Voucher` has no idempotency key and no duplicate error, and no
ACS call finds a voucher by our reference before it is on a pickup list
(`ACS_POD_FROM_REFERENCE_NO` needs a finalised voucher; manual, April 2026,
p. 7-9 and 21-22). A second request for one order mints a second voucher
(stage order 682, 2026-04-29). So the client sorts every failure by whether
the request may have reached ACS (`AcsClient._exchange`):

| Failure | Class | Mint |
|---|---|---|
| `ConnectTimeout`; `ConnectionError` wrapping a `MaxRetryError` whose reason is `NewConnectionError` / `NameResolutionError`; the request cap (`ACS request cap: ...`) | `AcsRetryableError` | never reached ACS: claim released, Celery retries |
| 403 / 406 | `AcsAuthError` | the manual's rejections (p. 1-2): claim released, Celery retries |
| an ACS voucher hold in force (`Not sent: ...`) | `AcsVoucherRequestHeldError` | never sent: no claim kept, `voucher_request_held_at` stamped, sent later by `issue_held_acs_vouchers` (below) |
| `ACSExecution_HasError`, a value row `Error_Message`, any other 4xx | `AcsAPIError` | ACS refused: claim released, `ops.shipment_rejected` |
| `ReadTimeout`, any other `ConnectionError` (urllib3's `ProtocolError`, a connection dropped after the body left, arrives as a plain `ConnectionError`), any 5xx, a non-JSON 200 | `AcsOutcomeUnknownError` | **unknown** |
| an exception the client does not classify; a 200 with neither `Voucher_No` nor `Error_Message`; a claim older than `_MINT_CLAIM_TTL_SECONDS` (a worker stopped between the request and the save) | | **unknown** |

Unknown sets `AcsShipment.voucher_request_unknown_at`, drops the claim,
notes the order, sends `ops.acs_voucher_request_unknown` once, and every
later call of `create_voucher_for_order` (the task, its retries, a
re-dispatch, "Issue ACS voucher now") raises
`AcsVoucherRequestUnknownError` without calling ACS. The task returns
`{"status": "voucher_request_unknown"}`. The dashboard counts the shipments
("ACS voucher requests to check", `attention=voucher_unknown` on the ACS
shipment list) whatever the email switch says.

To resolve: look in the ACS portal for a voucher with `Reference_Key1` =
the order id, created around `voucher_request_unknown_at`. The call itself
is ACS API Calls, alias `ACS_Create_Voucher`, the order id (outcome
`transport`, `retryable` or `unexpected`, no response). Then, on the
shipment:

- **Adopt ACS voucher** (number): refused when any shipment carries the
  number (its voucher, a multipart child, a previous or duplicate voucher).
  ACS must hold it for the store (an `ACS_Trackingsummary` row, p. 18-19,
  or an open "voucher matching no order" that ACS named on a refused
  pickup list); nothing overrides that. Then it must be this order's: the
  summary's `recipient` must equal the `Recipient_Name` the lost request
  sent (from `metadata["last_error"]`, else the `AcsApiCall` row, else the
  order), compared without accents, case or extra spaces. The manual
  documents no format for `recipient`: its example response is
  "ενδεικτικό" and every personal value in it is masked (`"XXXXXXXXXX"`).
  An exact match is the only automatic yes, and only while no other
  waiting order sent the same name. Everything else needs "Confirmed by
  hand", with how staff checked in the ACS portal: a different or masked
  name, a summary without a recipient, a number known only from a refused
  pickup list (which says nothing of which order), or a name another
  waiting order shares. That text goes on the order note, and
  `confirmed_by: "staff"` on the shipment. "Issue ACS voucher now" on a
  shipment of unknown outcome points to the action that applies: adopt or
  request again, or close when the order needs no voucher.
  The checks run again under the row lock; a concurrent adopt of the same
  number gets the same refusal. Saves the voucher, fetches multipart
  children, never calls `ACS_Create_Voucher`.
- **No voucher at ACS: request again** (a note on how you checked): clears
  the state and queues the mint once.
- **No voucher needed: close** (a note): for a shipment that is retired
  (CANCELED) or whose order is canceled or refunded. Adopt and request
  again refuse those; the shipment stays counted (ACS may hold a billed
  voucher) until closed. Nothing is sent to ACS; delete a lost voucher in
  the ACS portal or from "ACS vouchers matching no order", and say so in
  the note.

A late answer settles it too: when a worker's request outlives the claim's
TTL, another worker records the outcome as unknown, and the first one's
answer then arrives with a voucher, the save clears the flag (history:
"A late answer from ACS ... resolved ..."; an order note). Resetting a
canceled shipment for a re-mint clears a flag left beside a real voucher
and keeps one on a shipment with no voucher (it was canceled before the
lost request was resolved; both resolutions still work).

LogsQL: `phase:phase2_outcome_unknown` or `phase:phase1_stale_claim`.

## Voucher requests held while ACS is failing

An unknown outcome of a create the store sent begins an `AcsVoucherHold`
(admin: ACS voucher holds; `docs/order-system.md` §5.1 has the states).
While it is in force no `ACS_Create_Voucher` is sent for the store: the
task returns `{"status": "voucher_request_held"}`, the shipment carries
`voucher_request_held_at` (shipment list, `attention=voucher_held`) and
ACS API Calls shows no create for it: a request that was not sent is not
a call, so the creates listed during an outage are the ones that left. A
held order needs nothing from
staff. Only the orders whose request was really sent and lost are "ACS
voucher requests to check".

To see why a hold has not ended, read its row: `reason` is the last
failure (a lost answer, or an HTTP 4xx that was not ACS's own answer),
`failed_trials` how many times the one request sent first failed that
way, and "ACS is asked again after" the next time
`issue_held_acs_vouchers` asks ACS a read (`ACS_Get_Pickup_Lists` in ACS
API Calls, every tick after that time until one is answered). If ACS is
known to answer, press **ACS answers again: send one request** on the
hold. Only a voucher ends a hold: a refusal by ACS sends the next held
order next. A hold whose state reads "No request waits: the next one goes
alone" is waiting for an order, not for staff, and is not on the
dashboard.

LogsQL: `"ACS voucher hold"` or `"ACS voucher requests are held"`.

## A voucher ACS names that matches no order

A refused `ACS_Issue_Pickup_List` names every unprinted voucher of the
account (p. 14). One that is no shipment's voucher nor a multipart child
is kept as an `AcsUnrecognisedVoucher` (admin: ACS vouchers matching no
order), `ops.acs_unrecognised_vouchers` is sent the first time it is
named, and the dashboard counts it until staff resolve it: adopt it from
the order's shipment, **Delete at ACS** (`ACS_Delete_Voucher`, only while
it is on no pickup list, p. 11-12), or **Mark as checked** (made in the ACS
portal, for example). It may be the lost answer of a voucher request, a
duplicate, or a portal voucher: nothing documented ties it to an order, so
nothing is deleted or adopted automatically. Only a day with candidates
calls ACS, so a lone orphan surfaces on the next day that has one.

- While a mint of the store is in flight (a claim younger than its TTL),
  ACS may be naming the voucher it has just made for us, so the run records
  nothing and the next run decides.
- A shipment that saves a number (the mint, or an adopt) resolves the open
  row for it, children included.
- When the mint deletes its own duplicate at ACS (a late answer after
  another voucher was saved), the row for that number is resolved as
  deleted.
- **Delete at ACS** (a note, kept on the row) first turns every mint claim
  past its TTL into the unknown state (that request may still be waiting
  on ACS, and its answer could be this voucher), then re-checks under the
  row's lock. It is refused:
  - when a shipment's live voucher or child is this number;
  - while a mint is in flight;
  - while an order that still needs its voucher waits in the unknown state
    (the voucher may be that order's lost one, and a deleted voucher cannot
    be adopted). Resolve that order first, by adopting or requesting again.

  A recorded duplicate (`orphan_vouchers`) or an earlier voucher can be
  deleted, and so can a voucher while only orders that need none wait.
  ACS gives every create a new number, so only a request already sent when
  these checks run can answer with this voucher, and each such request is
  then visible. That is why there is no store-wide lock around the ACS
  call.

## What did ACS answer for call Y?

ACS API Calls: filter by alias, outcome and date, search the voucher or
reference, open the row: `request_params` (redacted), `response`, HTTP
status, duration. The log line carries the same outcome and `error_message`
(not the payloads).

LogsQL: `acs_alias:ACS_Create_Voucher outcome:business_error`

## Why did tracking stop?

1. Shipment Timeline: the last tracking event and the last
   `ACS_Trackingsummary` / `ACS_TrackingDetails` calls. Failing calls show
   their outcome; no calls at all means the poller skipped it (terminal
   shipment state, an order already DELIVERED, COMPLETED, CANCELED,
   RETURNED or REFUNDED, or `poll_acs_tracking_batch` not running).
2. `Stale alert sent` and `Last event at` on the Tracking tab: no event for
   `ACS_STALE_SHIPMENT_DAYS` days is reported by `check_stale_acs_shipments`;
   a voucher ACS never scanned looks like this.
3. LogsQL: `acs_alias:ACS_TrackingDetails voucher_no:<V> | sort by (_time)`.

## An ACS 406 / auth storm

ACS answers 406 to overlapping calls from one account; `acs_request_slot`
bounds that and `AcsAuthError` is retried. A storm is those failing anyway.

- Rate: `outcome:auth | stats by (acs_alias) count()` over the last hour.
- Spread across aliases and stores means the account or IP: a wrong
  `AcsApiKey` or an IP missing from the allow-list. One alias or store
  means concurrency; compare `duration_ms` of the neighbouring `ok` calls
  (long calls hold the slot).
- Admin: ACS API Calls, filter outcome Auth-layer rejection.

## COD reconcile

`reconcile_acs_cod_payouts` (daily) calls `ACS_COD_Beneficiary_Info` and
records `AcsCodPayout` rows. Checking it: ACS API Calls, alias
`ACS_COD_Beneficiary_Info`, its outcome and `response`; ACS COD Payouts for
the rows it produced and their reconciliation state; an overdue payout is
reported by `alert_unremitted_cod_payouts`.

LogsQL: `acs_alias:ACS_COD_Beneficiary_Info | sort by (_time) desc | limit 20`

See also `docs/order-system.md` (the ACS pickup-list section).
