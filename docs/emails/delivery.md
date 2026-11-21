# Emails: transports, the log, the webhook and the tests

## Transports (`transport.py`, `resend.py`)

`settings.EMAIL_TRANSPORT` (env, `smtp` or `resend`, anything else stops
boot) names the one transport that carries the pipeline's mail.

- **`smtp`** (default; production until the owner switches it, staging
  always): `EmailMultiAlternatives.send()` through the `default` mailer,
  `core.mail.DemoRecipientSuppressingBackend`, which hands the mail to the
  `relay` mailer. Both are in `settings.MAILERS` (Django 6.1); the `relay`
  is built from the same `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`,
  `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`/`EMAIL_USE_SSL`
  and `EMAIL_TIMEOUT` environment variables as before.
  SMTP returns no message id, so `EmailLog.provider_message_id` stays null:
  an environment fact, not a fallback.
- **`resend`**: `POST https://api.resend.com/emails` with `requests`, one
  request per recipient. The body is `from`, `to`, `subject`, `html`,
  `text`, `reply_to` and `headers` (the list headers, `List-Unsubscribe`
  included, only when non-empty) and `attachments` (`filename`, base64
  `content`, `content_type`). `Authorization: Bearer <key>` and
  `Idempotency-Key: <EmailLog.uuid>` (Resend keeps a key for 24 hours).
  The returned `id` is recorded as `provider_message_id`.
  Two hidden **tags**, never headers: `schema=<schema name>` and
  `log=<EmailLog.uuid>`; they come back in the webhook's `data.tags`, which
  is how an event finds its row. No address is in a tag, header or log line.

A demo store's own mailboxes are dropped on both transports
(`core.mail.without_demo_mailboxes`); a message left with no recipient posts
nothing.

### Which Resend team

One Resend team per store that has its own verified domain (owner decision
D6); the platform's team carries the platform's own mail and every store
without its own domain.

- The store sends as itself when `emails.sender.own_sender()` is set: its
  `from_email` is set **and** `from_email_verified` is ticked. Then the key
  is `Tenant.resend_api_key` and **only** that: a store with its own domain
  and no key raises `ResendNotConfigured` (an `ImproperlyConfigured`, not a
  class a task retries; the row is `FAILED`) instead of borrowing the
  platform's, whose team does not own the domain.
- Any other store, and every platform type (sent under `public`), uses
  `settings.RESEND_API_KEY`. Empty raises `ResendNotConfigured`.
- `Tenant.resend_api_key` and `Tenant.resend_webhook_secret` are
  Fernet-encrypted (see `secret-encryption.md`), editable by platform staff
  only (the "Email & Security" tab of the platform's tenant admin); a store
  role sees "set / not set". The webhook secret is read by the webhook
  receiver (see The Resend webhook).
- `RESEND_API_KEY` is required in production **and staging** when
  `EMAIL_TRANSPORT=resend`; the `EMAIL_HOST_PASSWORD` environment variable
  is required only for `smtp`. The `EMAIL_TIMEOUT` environment variable
  (`settings.EMAIL_SEND_TIMEOUT`) bounds both the SMTP socket and the
  Resend request.

### Guarding the switch (`src/emails/setup.py`, `src/emails/checks.py`)

A store that sends from its own verified domain and has no key cannot send
over Resend at all, so selecting `resend` while one exists would fail all its
mail for good. One definition (`setup.lacks_resend_key`, `stores_lacking_resend_key`:
Resend selected, active and not suspended, `from_email` set and verified, no
`resend_api_key`, one query on `public`) feeds three guards:

1. **Deploy guard.** `emails.E001`, a `database` system check, is an error
   naming each store and its domain. `migration_preflight` (step 0 of the
   PreSync hook, and CI) runs the database checks first, so a bad switch
   fails the hook before any pod rolls and the previous release keeps sending
   over SMTP. The web process does not query at boot; settings import only
   checks `RESEND_API_KEY`.
2. **Validation at the source.** `Tenant.clean` refuses a save that leaves a
   verified own-domain store without its key while Resend is selected: both
   ticking From Email verified without a key and clearing the key of a
   verified store (error on `resend_api_key`).
3. **Dashboards.** The platform dashboard (`PlatformAlerts`) lists each such
   store ("Emails can't be sent from <domain> until its Resend key is
   saved") with a link to it; the store's own dashboard (`StoreAlerts`, for
   whoever may change store settings, OWNER and ADMIN) tells the merchant
   that its emails are not being sent and to contact support, since only the
   platform can fix it.

All three are inert while `EMAIL_TRANSPORT=smtp`.

### Failures

Every failure is a `requests` exception, so `core.retry` decides as for any
HTTP call. A timeout, a dropped connection, a 5xx and a 429 are retried by
the calling task (`MAIL_TRANSIENT_ERRORS`); any other 4xx is permanent
(`is_permanent_http_failure`). The exception text is `Resend answered 422
validation_error`: the status and Resend's error name, never its message
(which can quote the address) or the request. The key is never logged.

The idempotency key is the delivery's uuid (see Delivery identity across
retries), so a task retry after a lost response sends the same key and Resend
answers with the first message instead of sending a second one.

## The email log (`EmailLog`, `log.py`)

One row per recipient's delivery, in the schema the mail is sent under: a
store's rows in its schema, a platform type's in `public`. `send_email`
creates it before the transport runs (`QUEUED`), so its `uuid` can travel
with the message (the idempotency key and hidden tag of the Resend
transport), then sets `SENT` (`sent_at`, `provider`, `provider_message_id`;
SMTP returns no id, so it stays null there) or `FAILED` (`error`). A skip is
one row with its status and, for a closed gate, the gate's label in `reason`.

**Delivery identity across retries.** `EmailLog.uuid` is the delivery's identity (`log.delivery_id`). Inside a Celery
task it is derived from the task id plus the type, the recipient hash and the
subject; outside a task it is a fresh uuid4. Every copy of a task carries the
same id: its retries, a message delivered again after a worker was lost, and
the sweep's copy of a publish that also wrote its message
(`tenant.celery.dispatch_on_commit` picks the id, see
`docs/deferred-dispatch.md`). A copy of a send that failed (or whose response
was lost) therefore finds the row the earlier one wrote, moves it back to
`QUEUED` and sends under the same uuid, so one row and, on Resend, one
idempotency key cover the whole chain. A recipient whose row is already `SENT`
is returned as sent without a second send: a task that mails two recipients
and fails on the second does not mail the first again, and a second copy of a
task mails nobody. Two different mails of one type to one recipient in one
task differ in their subject, so they stay two deliveries.

**Copies at the same moment.** The sweep's copy and the message that publish
wrote are delivered together once the broker takes messages again, so two
workers can send the same delivery at once. `log.delivering` reads the row and
the transport sends under a PostgreSQL advisory lock on the delivery id
(`core.db.locks.held_across_calls`): the second copy waits until the first has
the transport's answer, then finds the row `SENT` and sends nothing. The lock is the session's, held outside any
transaction (a transaction open across the send would be ended by
`idle_in_transaction_session_timeout`), so a worker that dies takes it along
and the waiting copy sends instead: at once when its process ends, and within
about a minute when its node is lost without closing the socket (every session
sets TCP keepalives, `DB_TCP_KEEPALIVES_*` in `src/settings/database.py`; the
server's own probe a lost client for about half an hour). Inside a transaction (a task applied
eagerly) it is the transaction's and ends with it. A send outside a task has a
fresh id and takes no lock. A wait that runs out (`statement_timeout`, or a
`lock_timeout` a session sets) raises
`OperationalError`, which the mail tasks retry; a database that cannot be
reached at all does not stop the send, as for the row below.
`tests/integration/emails/test_delivery_copies_at_once.py` runs two copies on
two connections.

What a row holds: type key, status, language, the subject that went out, a
keyed hash of the address (`optout.address_hash`) and a masked form
(`j***@e***.com`). **It holds no address and no body**: a body is
re-rendered from the type and its object, and `error` is the failure's class
and SMTP or HTTP code, never its message (a relay's text can quote the
recipient). `tests/integration/emails/test_email_log.py` scans every column.
**The log never stops or changes a send.** A write that fails (the insert
before the send, the update after it, a skip) is reported as `Email <type> is
not logged` and swallowed: raising would keep an alert about a failing
database from going out, and would make the task retry, and so send twice, a
mail that has already left. The send then simply has no row.

`EmailEvent` (the provider timeline of one row, unique on the provider's
event id) and `EmailSuppression` (an address hash with a reason and a scope,
kept until removed) are filled by the webhook (next section).

## The Resend webhook (`webhooks.py`, `events.py`, `suppression.py`)

`POST /resend/webhook/`, mounted on **every** host (root-shared, like the Viva
and BoxNow receivers), because each Resend team registers the host that owns
its keys:

- a store with its own team registers its own API host and the delivery is
  verified with that store's `Tenant.resend_webhook_secret`, resolved by the
  host it arrived on;
- the platform's team registers the platform host, verified with
  `settings.RESEND_WEBHOOK_SECRET`; its events are routed by their signed
  `schema` tag to the store (or `public`) that sent the mail.

There is no fallback between the two secrets, and a host with none answers
503 (Resend retries until it is set).

1. **Verification** (`webhooks.verify_signature`): the **Standard Webhooks**
   library's `Webhook(secret).verify` (the `standardwebhooks` package, the
   reference verifier Svix maintains and the `svix` client itself delegates to;
   it has no dependencies of its own). Resend signs with Svix, whose three
   headers are `svix-id`, `svix-timestamp` and `svix-signature`; the adapter
   renames them to the `webhook-` names the library reads. The library checks
   HMAC-SHA256 over `"{id}.{timestamp}.{raw body}"` under the secret (`whsec_` +
   base64), accepts any one of the `v1,` signatures in the header (several while
   a secret rotates), compares in constant time, and refuses a timestamp more than
   five minutes either way, which is the replay guard (fixed by the library, so
   there is no setting). Everything it can raise for a request that is not a
   delivery (a missing header, a malformed signature or secret, a body that is not
   UTF-8) becomes one `InvalidSignature`, so a forged request is a 400, never a
   500. The adapter is pinned by `tests/unit/emails/test_webhook_signature.py`
   against a signature made by the `svix` package itself. The body is verified
   before it is parsed, and capped at `RESEND_WEBHOOK_MAX_BODY_BYTES` (413). 400
   for anything else, with no payload in the log.
2. **Queue.** The view answers 200 at once and queues
   `emails.tasks.process_provider_event(svix_id, payload)`, which runs in the
   schema of the host that received it.
3. **Routing** (`events.route`): a store's endpoint speaks only for that
   store (a tag naming another schema is dropped), the platform's routes by
   the tag to `public` or an existing store, an unknown store is dropped with
   a warning.
4. **Matching** (`events.process`): the row must match BOTH the `log` tag
   (the row's uuid) and `data.email_id` (the provider's id), so a valid event
   can never update another row. A row not there yet (a fast event can beat
   the send's write of the id) retries up to four times with backoff, then
   ends as a recorded task failure.
5. **Dedupe:** `EmailEvent.provider_event_id` is the `svix-id`, unique per
   schema; a repeated delivery is recorded once and changes nothing.
6. **Effect:**

| Event | Row status | Also |
|---|---|---|
| `email.sent` | unchanged | recorded |
| `email.delivered` | `delivered` | |
| `email.delivery_delayed` | `delayed` | |
| `email.bounced`, type `Permanent` | `bounced` | `EmailSuppression` (hard bounce, scope all) |
| `email.bounced`, any other type | `delayed` | |
| `email.complained` | `complained` | `EmailSuppression` (complaint, scope marketing, D7) and an `EmailOptOut` for every marketing type |
| `email.failed` | `failed` | the reason token in `error` |
| `email.suppressed` | `provider_suppressed` | `EmailSuppression` (provider, scope all) |

   The status only moves forward (`events.RANK`: sent, delayed, delivered,
   failed, bounced or suppressed, complained), because events arrive out of
   order. `detail` keeps the bounce type and sub-type, the failure reason or
   the suppression type as fixed tokens, never an address or the provider's
   message. Open and click events are not subscribed (D9); one that arrives is
   recorded and changes nothing.

**Suppression at send time.** `send_email` asks `suppression.blocks` before it
renders: `all` blocks every type, `marketing` only a marketing one. A blocked
recipient is a `skipped_suppressed` row and `Delivery`. A required mail (a
receipt, a security code) to an `all`-suppressed address is not sent either,
and is logged at WARNING so it is never lost silently. A wider scope replaces
a narrower row (a bounce after a complaint), never the reverse. Removing a row
is the admin's (PR 3.4), and must also remove the address in Resend's own list.

**Retention.** `EMAIL_LOG_RETENTION_DAYS` (store setting, default 90, `0`
keeps everything, validator `emails.log.validate_retention_days_setting`).
`emails.tasks.purge_expired_email_logs` deletes through
`core.retention.delete_in_batches`, in public from beat and per store through
`tenant.tasks.fanout_purge_expired_email_logs` (`include_suspended=True`);
events cascade, suppressions stay. Staging anonymisation deletes all three
tables (`src/devtools/anonymise/rules.py`).

## Not built yet

- A deferred (`defer`) send; there is no `X-Entity-Ref-ID`.
- The order note that tells staff a receipt was not sent because the address
  is suppressed (it needs a hook on `EmailType` the order types implement),
  and moving a newsletter subscription to `BOUNCED` on a hard bounce (the
  event carries an address, the log only its hash).
- "Edit the wording" on an email's page (Step 4). A real object is chosen only
  for the order types, every other type previews its `sample`.

## Tests

- `tests/unit/emails/`, `tests/integration/emails/`, `tests_mt/test_email_isolation.py`:
  the registry, gates, switch, opt-out, headers, header safety and the
  per-schema isolation. The contract is proven over probe types
  (`tests/utils/emails.py`): the `catalogue` fixture swaps the registry's
  contents for the test and restores them. Never register a probe type in
  production code. A test that runs a store email (an order task) needs a
  store bound: `tests.utils.staff.store_tenant` + `bind_store_tenant`.
- `tests/unit/emails/test_webhook_signature.py`, `tests/integration/emails/test_resend_webhook.py`
  and `tests_mt/test_email_webhook_routing.py`: verification (changed byte,
  stale timestamp, other secret, no fallback between the two secrets, 503,
  413), every event's effect, the forward-only status, dedupe, matching on
  both ids, suppression scopes, `send_email` honouring the list, and the
  cross-schema isolation with real schemas.
- `tests/integration/emails/test_resend_setup_guard.py`: who is lacking a key
  (one query), the system check and `migration_preflight` failing, the
  `Tenant.clean` refusal, both dashboards and their roles.
- `tests/integration/emails/test_resend_transport.py`: the request (key,
  idempotency key, tags, attachments, headers), which key a store uses, the
  retry verdict of each failure, demo suppression. HTTP is mocked at
  `requests.post`.
- `tests/integration/emails/test_email_log.py`: a row for every outcome, no
  raw address in any column, a failure never reaches the row, the retention
  sweep. `tests_mt/test_email_isolation.py` proves a platform type's row is in
  `public` and a store's in its own schema.
- `tests/integration/emails/test_golden_render.py` records every order
  email as it is sent today (subject, recipients, From, Reply-To, headers,
  attachment names, HTML and text, in Greek and English) under
  `tests/integration/emails/golden/`. A send site that moves onto the
  pipeline must keep these equal; a deliberate change re-records them in the
  same commit (`GOLDEN_UPDATE=1 uv run pytest <test>`, helper
  `tests/utils/golden.py`). Everything a body prints is pinned; the only
  normalised value is the footer's copyright year. The Greek files need
  the compiled catalogue (`manage.py compilemessages --ignore=.venv`); the
  test fails with that instruction rather than recording English.
- `tests/integration/emails/test_golden_render_domains.py` does the same
  for the product, gift card, B2B, contact, account and cart emails. It
  pins what the rows cannot (the user and product ids, the signer's clock,
  `SECRET_KEY`, `EMAIL_HASH_KEY`) because the factories draw at random.
- `tests/unit/emails/test_only_transport_sends.py` reads every module's
  syntax tree and fails when a module outside `src/emails/transport.py` imports or
  calls `EmailMessage`, `EmailMultiAlternatives`, `send_mail`,
  `send_mass_mail`, `mail_admins` or `mail_managers`: a send site that skips
  the pipeline skips the switch, the opt-out and the headers.
- `tests/integration/emails/test_golden_render_platform.py` records the
  platform's mail. The billing notices and the staff reset were recorded on
  the old send paths and kept equal, but for the deliberate differences (a
  named `From`, the platform's brand on the staff reset); the operator mail
  had no template, so its files record what it is now. It pins the platform
  name, sender, contact address and static host so the files do not follow
  `.env`.
- `tests/integration/emails/test_golden_render_allauth.py` records the
  19 allauth mails through the adapter, and fails for a type with no
  case. `tests/integration/user/test_allauth_flows_use_the_pipeline.py`
  drives the real headless endpoints (sign-up, password reset, login
  code), and `test_allauth_email_store_identity.py` discovers the kinds
  from the installed allauth and fails for one that is not mapped.
- `tests/integration/emails/test_golden_render_ops.py` records the
  operational alerts through the real code that sends them. They had no
  template and no translation before, so the files record what they are now.
- `tests_mt/test_platform_email_pages.py`: the control plane's page with real
  schemas (only the platform's emails, the `public` figures, log and suppression
  list, no orders, Send test to the operator and logged in `public`, throttle,
  no toggle, the dashboard alert, the sidebar entry, cached figures never shared
  across schemas).
- `tests/integration/emails/test_email_pages.py`: the status page (tabs,
  search, state filter, figures against a hand-computed fixture, flat queries),
  one email's page, the Send test (only the signed-in account, throttle,
  marked as a test), the log (masked, exact-address search, filters, read-only),
  the suppression list and its removal, and the dashboard count.
- `tests/integration/emails/test_switch_admin.py`: the page's permissions
  (ADMIN and OWNER switch, STAFF only look), toggling changes
  what `send_email` does, history, the cache generation, and a required or
  unknown type refused.
- `tests/integration/emails/test_ops_alerts.py`: each real sender honours
  its switch and keeps its bookkeeping, a store with nobody to write to skips
  the mail, and carrier code imports no `send_mail`.
  `test_import_ops_alert_switches.py` runs `emails/0003` over the settings a
  store is in.
- `tests/integration/emails/test_every_type.py` is parametrised over
  `registry.all_types()`, so a type added to any `src/<app>/emails.py` is covered
  without editing a test: its sample sends, a closed gate stops it with the
  gate's label, a store's switch stops a switchable one and refuses a
  required one, the headers are exactly those of its audience and kind, an
  opt-out stops marketing mail and only that, and the preview is the mail
  sent.
- `tests/integration/emails/test_domain_tasks.py`: what each migrated task
  does with a skip (alerts stay armed, claims are released or settled).
- Add a golden case (the helper takes any sent message) before moving
  another domain's send sites onto the pipeline. A test about what an email
  says rather than whether it is sent uses the `open_email_gates` fixture.
