# Staging anonymisation

`manage.py staging_anonymise` replaces every customer identity in a database
restored from production, so staging keeps real orders, products, stock and
amounts but no real person. It is step 4 of
`grooveshop-infrastructure/scripts/staging-refresh.sh` (the infrastructure
repo's `docs/staging.md` has the operator side).

It exists because the refresh used to copy production unchanged. Decision of
2026-10-17: staging keeps more than identities alone, and **every** customer
identity is replaced.

## Where it runs

The refresh restores the dump into a scratch database, runs this command
against it (`DB_NAME` overridden for that one `kubectl exec`), and only then
swaps the scratch database in under the name the staging services use. No
staging pod, worker or backup ever reads an un-scrubbed database. If the command
fails, the refresh drops the scratch database and stops.

The command refuses to run unless `SYSTEM_ENV` is `staging`, and
`devtools.anonymise.engine.anonymise_database` refuses again, so no caller can
skip the check. Production's value is `production`.

## What it does

The whole run is one transaction over the public schema and every tenant
schema (discovered from the `Tenant` table; a schema with no tenant row fails
the run). It uses `QuerySet.update`, `bulk_update` and `delete` only, never
`save()`, so no signal fires: nothing is indexed, mailed, queued or
cache-invalidated by the rewrite, and `updated_at` keeps its value.

- **Scrubbed in place**, with deterministic fakes: names, e-mail addresses
  (`...@staging.invalid`), phones, usernames, addresses, company and VAT data,
  IP addresses, user agents, free text a customer typed, and the personal keys
  inside carrier and audit JSON. Passwords become unusable. Avatars and birth
  dates are cleared.
- **Bearer credentials that work on production** are replaced by a staging
  twin that cannot equal a production value: every gift-card code and every
  personal (user-assigned) promotion code becomes `STG-` plus a digest, and
  the guest order, guest cart and gift-card purchase UUIDs become version 5
  UUIDs (production's are random version 4). A twin is a pure function of its
  original, so `cart_uuid` in order metadata and recommendation events still
  matches its cart, and a second run changes nothing. Public promotion codes
  are marketing and stay. The residual scan also searches for the originals.
- **Deleted**: sessions, Knox tokens, MFA and WebAuthn authenticators, social
  accounts and tokens, e-mail confirmations, OIDC tokens, GDPR export rows,
  search queries, notifications, stock reservations, admin log entries,
  Conversions API logs, Celery results, and every dj-stripe mirror row except
  the platform's own Stripe account and endpoint configuration.
- **Kept real**: orders, items, payments, invoices' amounts, products, stock,
  prices, promotions, gift-card balances, loyalty points, translations.

Fakes are a pure function of a key, so the same account, its orders and its
addresses resolve to one fake person, a second run changes nothing, and a
tester's bookmark of an order keeps meaning the same order. Names come from
hand-authored Greek pools in `devtools/anonymise/pools.py` (Faker is a dev-only
dependency and is absent from the deployed image).

### What keeps working

- **Platform staff.** In the public schema, `user_useraccount` is the platform
  table: accounts with `is_staff`, `is_superuser` or a tenant membership are
  left untouched, passwords included. Any other public account is residue and
  is scrubbed.
- **The shared demo logins.** A store with `DEMO_ACCOUNT_ENABLED` keeps the two
  accounts named by `DEMO_ACCOUNT_EMAIL` and `DEMO_ACCOUNT_B2B_EMAIL`, with
  their rows, so the sign-in strip on the demo storefront still works.

## How coverage is guaranteed

Three layers, each failing differently.

1. **`devtools/anonymise/rules.py`** resolves every model to a `Scrub`, a
   `Delete`, or a `NOT_PERSONAL` entry with a reason.
2. **Guard tests** (`tests/unit/devtools/test_anonymise_covers_every_model.py`)
   fail CI when a model has no decision, when a field that looks personal (an
   e-mail, phone or IP field, or a name-shaped column) is neither scrubbed nor
   named in `NOT_PERSONAL_FIELDS`, and when GDPR erasure
   (`user.services.gdpr.anonymise_and_delete_user`) handles a model this table
   calls not personal. It reuses the erasure test's own detector, so the two
   cannot drift.
3. **The residual scan** (`devtools/anonymise/scan.py`) runs at the end of every
   refresh. It collects every original e-mail, phone, IP and full name the run
   replaced, then reads every text, JSON and inet column of every table in
   every schema, including tables no rule mentions, and fails the run (rolling
   it back) if one survived. It reports `schema.table.column` and a count,
   never the value. It cannot search for addresses or VAT numbers (too common a
   string to fail a refresh over); the rules still replace them.

The scan is the guarantee; the rules and guard tests are what keep it from
failing in practice.

## Adding a model or a field

A new model fails `test_every_model_is_scrubbed_deleted_or_declared_not_personal`
until you decide:

- it holds a person's data: add a `Scrub` to `REGISTRY` naming the fields and
  their `Kind`, and the foreign key (`user_fk` / `order_fk`) that says whose
  data a row is, so it resolves the same fake person as the order or account;
- it is personal behaviour or a credential with no value on staging: `Delete`;
- it holds nothing about a person: add it to `NOT_PERSONAL` with the reason.

A new e-mail, phone or IP field fails the field test the same way. A field
that really is not personal goes in `NOT_PERSONAL_FIELDS` with the reason.

## Tests

- `tests/unit/devtools/test_anonymise_covers_every_model.py` — the guards.
- `tests/unit/devtools/test_anonymise_values.py` — fakes, redaction, scanner.
- `tests_mt/test_staging_anonymise.py` — two seeded tenant schemas plus the
  public schema: no original value survives in any table (read back
  independently of the command's own scanner), staff and demo logins kept,
  amounts and links real, an unruled leak fails and rolls back, production is
  refused, an orphan schema is refused, a second run changes nothing.

```bash
DB_NAME=gs_staging_scrub uv run pytest tests/unit/devtools -n0
DB_NAME=gs_staging_scrub uv run pytest tests_mt/test_staging_anonymise.py -n0
```

## Not covered

- **Files.** Avatars are cleared in the database; the refresh script also keeps
  `uploads/users` out of the media copy. Contact attachments, invoice PDFs and
  GDPR export bundles live under the private media root, which the refresh
  does not copy.
- **Credentials that are not bearer values of a customer.** The Stripe API key
  rows, platform staff tokens and the merchants' encrypted payment and carrier
  credentials on the `Tenant` row are copied as they are (see the
  infrastructure repo's `docs/staging.md`).
- **Store contact details.** A store's published contact and sender mailboxes
  stay (`NOT_PERSONAL_FIELDS`); its owner e-mail and carrier notification phone
  are replaced.
