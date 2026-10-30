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
ACS one). Credentials (`Company_ID`, `Company_Password`, `User_ID`,
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
