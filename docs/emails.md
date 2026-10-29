# Emails

The email pipeline: one catalogue of email types, one function that sends
them, and the switch and opt-out a store or a recipient uses to stop one.
Source: `emails/` (`types.py`, `registry.py`, `gates.py`, `recipients.py`,
`sender.py`, `headers.py`, `optout.py`, `render.py`, `send.py`,
`transport.py`, `models.py`, `services.py`), `tests/utils/emails.py` and
`tests/utils/golden.py` (the test helpers). The code wins over this page.

**State.** The pipeline exists and is tested, but no production type is
registered yet: every email the platform sends today still goes through its
own task (`order/tasks.py`, `shipping_acs/tasks.py`, ...) and
`core/email/` (the admin preview). They move onto the pipeline one domain
at a time, each move keeping the recorded golden files equal (see Tests).
Until then `emails.registry.registry` is empty and `EmailsConfig.ready`
freezes it empty.

## The pieces

| Module | What it does |
|---|---|
| `types.py` | `EmailType`, the contract of one kind of email, and `Audience`, `Kind`, `Owner`, `RenderEnv`. |
| `registry.py` | `registry`: `register`, `freeze`, `get`, `all_types`, `types_for`. |
| `gates.py` | The four gates a type can list. |
| `recipients.py` | `Recipient(address, language)` and the resolvers `customer_of`, `store_operators`, `platform_operators`. |
| `sender.py` | The `From` and `Reply-To`. Reuses `tenant.credentials`. |
| `headers.py` | `build_headers`: the list headers by kind. Reuses `user.utils.subscription`. |
| `optout.py` | The address hash, `is_opted_out`, `opt_out`, the signed unsubscribe token. |
| `render.py` | `render`: subject and both bodies in one language; `clean_header_text`. |
| `transport.py` | Builds the `EmailMultiAlternatives` and sends it; the pipeline's only sender. |
| `send.py` | `send_email`: the whole envelope. |
| `models.py`, `services.py` | `EmailSwitch`, `EmailOptOut`; `set_switch`, the only writer of switches. |

`emails` is in both `SHARED_APPS` and `TENANT_APPS`: a store's switches and
opt-outs live in its schema, the platform's own in `public`. It imports no
domain app: each app declares its types in `<app>/emails.py`, next to the
data its builder reads. `EmailsConfig.ready` imports every app's `emails`
module (`autodiscover_modules`), then freezes the registry.

## An email type

`EmailType` is a frozen, keyword-only dataclass:

- `key`: `order.shipped`, lower-case words joined by dots, at most 64
  characters. It is the switch key, the opt-out key and the default
  `List-ID` (dots become dashes) unless `list_id` is given.
- `title`, `description`, `area`: what the admin will show and group by.
- `audience` (`customer`, `store_staff`, `platform_operator`), `kind`
  (`transactional`, `marketing`), `owner` (`store`, `platform`).
- `required`: legally or functionally required. Never switchable, and it
  cannot be marketing.
- `templates`: `emails/order/order_shipped`, with `.html` and `.txt` beside it.
- `subject`: a `gettext_noop` msgid with plain named fields only
  (`{order_id}`: no attribute or index access, no conversion or format spec;
  checked when the type is built). `render.format_subject` substitutes
  `str(context[name])` and evaluates nothing. A translation that is invalid
  or names another field is logged and the msgid is used, so one bad
  msgstr (.po, Rosetta overlay) cannot stop an email. The result is cleaned
  (see Header safety).
- `build_context(obj, env) -> Mapping`: the one reader of the domain, for
  the sent mail and the preview alike. Runs under the recipient's language.
- `sample(env)`: an unsaved object graph the preview renders.
- `recipients(obj) -> Sequence[Recipient]`.
- `gates`: every gate must be open.

`switchable` is `not required`; `opt_out_applies` is `kind is MARKETING`.

A type that breaks a rule is not constructed (`ImproperlyConfigured`, every
broken rule named): a bad key, a required marketing type, a platform type
for customers. The registry refuses at `register` a duplicate key, and at
`freeze` a missing `.html` or `.txt`, and a template file that sits in a
directory some type uses while no type names it. Only directories the
catalogue already uses are scanned, so the templates of mail that has not
moved onto the pipeline are none of its business.

## `send_email(type_key, obj, *, recipient=None)`

Returns one `Delivery(type_key, status, reason, recipient)` per recipient,
or a single skip. In order:

1. The type is looked up (`UnknownEmailType` if absent).
2. **Owner.** A `platform` type runs under `schema_context(public)`. A
   `store` type needs a bound store and raises `StoreRequired` without one:
   there is no fall back to the platform.
3. **Gates**, in the order the type lists them. The first closed one gives
   `SKIPPED_FEATURE_OFF` with its label as `reason`.
4. **Switch.** A switchable type the store turned off gives
   `SKIPPED_SWITCHED_OFF`. A required type never reads the switch.
5. **Recipients**: `recipient`, else `email_type.recipients(obj)`. None
   gives `SKIPPED_NO_RECIPIENT`.
6. Per recipient: **opt-out** (marketing only, `SKIPPED_OPTED_OUT`), then
   `render` in `recipient.language`, `build_headers`, and
   `transport.deliver`.

A transport failure is logged (no address in the line) and re-raised, so the
calling task's own `autoretry_for=MAIL_TRANSIENT_ERRORS` applies. The
caller keeps its idempotency claim and its bookkeeping: the pipeline does
not take over business de-duplication.

The skips are logged as a structured line (`email_type`, `status`,
`reason`). There is no log table yet.

### Language

The language is the recipient's. The resolvers state it: `customer_of`
(an `Order` or a `UserAccount`: its own `language_code`, else the account's,
else the store's), `store_operators` (the store's), `platform_operators`
(`settings.LANGUAGE_CODE`). `store_operators` is the bound tenant's
`contact_email` then `owner_email` and nothing else: a store with neither
has nobody to write to, and `settings.ADMINS` is never used for it.

### Sender

`From` is `tenant.credentials.tenant_from_email()` read in the schema the
send runs under, so a platform type is sent as `"{SITE_NAME}"
<DEFAULT_FROM_EMAIL>` and a store type as the store (its own verified domain,
else its name on the platform's sender). `Reply-To` is the store's contact
address for a store type and empty for a platform type.

## Gates (`gates.py`)

Each gate has a `label` (the reason a store reads, "Gift cards feature")
and `is_open()`. All are frozen, keyword-only dataclasses.

| Gate | Open while |
|---|---|
| `FeatureGate(feature=...)` | `tenant.features.feature_on(feature)`; the name must be in `STORE_FEATURES` (checked when the gate is built). |
| `StoreSwitchGate(setting=...)` | a server-side store setting is on (`MYDATA_ENABLED`); the name must be declared in `EXTRA_SETTINGS_DEFAULTS`. |
| `CarrierGate(carrier=...)` | the `ShippingProvider` with that code is active. |
| `ScheduleGate(entry=...)` | the `django_celery_beat` `PeriodicTask` of that name is enabled (read in `public`). |

## The store's switch

`EmailSwitch` is sparse: one row per type a store turned **off**. A type
with no row is on, so a new type needs no data migration. Rows are written
only through `emails.services.set_switch(user, type_key, enabled=...)`:
it needs `emails.change_emailswitch`, and `EmailSwitch.clean` refuses a
required type (`email_type_required`) and an unknown one
(`unknown_email_type`). Switching a type on removes the row. Both changes
are in simple-history with the staff member.

The list of switched-off keys is cached as
`emails:switches:<tenant.cache_generation>`; a write moves the generation
(`tenant.signals.bump_generation_on_email_switch_change`), so the next
request or task sees it. A request that already holds the store row keeps
the old list until it ends. The public schema has no generation and reads
the table directly.

## Opt-out (`optout.py`)

Only marketing types honour an opt-out; a receipt or a security mail is not
something a person can decline.

- `EmailOptOut(address_hash, type_key, source)` is unique per
  `(address_hash, type_key)`. `type_key` is a marketing type's key, or `*`
  for every marketing type.
- `address_hash(address)` is HMAC-SHA256 of the stripped, lower-cased
  address under `settings.EMAIL_HASH_KEY`: the table holds no address.
- `make_token(address, type_key)` signs `{schema, h, t}` with
  `django.core.signing` under the salt `emails.optout`. It names the schema
  because `SECRET_KEY` is global: a token from one store does not verify on
  another. `read_token` / `opt_out_by_token` raise `InvalidToken` for a bad
  signature, an expired token (365 days), another store, an unknown type or
  a type that takes no opt-out.
- `unsubscribe_url` builds `<api host>/api/v1/emails/unsubscribe/<token>`
  (`UNSUBSCRIBE_PATH`). **No view serves that path yet**: a marketing type
  must not be registered before it does.

### `EMAIL_HASH_KEY`

A required environment variable in production **and staging** (any
non-dev, non-CI deployment: the process refuses to start without it), so it
must exist in the environment BEFORE the release containing `emails/`
deploys. Development and CI get a fixed insecure key. Generate one
with `secrets.token_urlsafe(48)` and keep it with the other platform
secrets. It is separate from `SECRET_KEY` on purpose: rotating `SECRET_KEY`
must not orphan the opt-outs. Changing `EMAIL_HASH_KEY` forgets every
opt-out (the hashes no longer match, and outstanding unsubscribe links
stop working), so it is never rotated casually.

## Headers (`headers.py`)

`build_headers(email_type, recipient)` decides by audience, then by kind.
Only `customer` mail is list mail; `store_staff` and `platform_operator`
mail gets no list headers (`{}`), as staff mail has none today. For
customers:

- **Transactional**: `List-ID: <{list_id}.{store domain}>` and, when the
  store has a contact address, `List-Unsubscribe: <mailto:{contact}?subject=unsubscribe>`.
  Never `List-Unsubscribe-Post`: there is no programmatic unsubscribe for
  a receipt.
- **Marketing**: `List-Unsubscribe` with the `mailto:` form (when the store
  has a contact address) and the https unsubscribe URL,
  `List-Unsubscribe-Post: List-Unsubscribe=One-Click` and `List-ID`
  (RFC 2369, RFC 8058, Google's bulk-sender rules).

The builders are `user.utils.subscription.build_transactional_list_headers`
and `build_list_unsubscribe_headers`, reused; they move here when the send
sites that call them do.

## Header safety

A subject is the only header built from data a store writes (a product or
company name). `render.clean_header_text` turns every whitespace run
(carriage return and line feed included) into one space, drops control
characters and caps the length at 200, so a name containing `\r\nBcc: x`
cannot add a header. `transport` relies on Django's own refusal as the
backstop: an address with a line break raises `BadHeaderError`. Data-derived
subject pieces reach the subject through the context builder, never by
concatenation at a call site.

## Not built yet

- Attachments: `send_email` and `transport` cannot attach a file (the
  receipt and credit PDFs need it).
- The unsubscribe endpoints behind `UNSUBSCRIBE_PATH`; marketing headers
  already point at them.
- `EmailLog`, provider events, suppression and a deferred (`defer`) send;
  skips are only a structured log line, and there is no `X-Entity-Ref-ID`.
- The import move: `sender.py` and `headers.py` reuse
  `tenant.credentials` and `user.utils.subscription`, whose code moves here
  with the send sites (the header import will then need its cycle broken).
- Every production email type, the admin page and the preview.

## Tests

- `tests/unit/emails/`, `tests/integration/emails/`, `tests_mt/test_email_isolation.py`:
  the registry, gates, switch, opt-out, headers, header safety and the
  per-schema isolation. The registry is empty in production, so the
  contract is proven over probe types (`tests/utils/emails.py`): the
  `catalogue` fixture swaps the registry's contents for the test and
  restores them. Never register a probe type in production code.
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
- Add a golden case (the helper takes any sent message) before moving
  another domain's send sites onto the pipeline.
