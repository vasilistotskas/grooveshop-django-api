# CHANGELOG




## v1.113.0 (2026-04-25)

### Features

* feat(storefront): admin-toggleable RECENTLY_VIEWED_ENABLED setting

Lets admins enable/disable the home page "Είδες Πρόσφατα" rail
without a deploy. The viewing history itself stays in client-side
localStorage; this flag only controls whether the rail renders.

- settings.py: add RECENTLY_VIEWED_ENABLED to EXTRA_SETTINGS_DEFAULTS
  (type=bool, default=True) so legacy behaviour is preserved on first
  upgrade.
- core/api/views.py: whitelist the key in PUBLIC_SETTING_KEYS so the
  unauthenticated home page can read it via /settings/get.

Toggle path for admins: Django admin → Extra Settings → Settings →
flip the value_bool column on the RECENTLY_VIEWED_ENABLED row. Effect
is immediate via the existing post_save → cache-update signal.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`b12c07b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b12c07bbf82f67153a8a4663df4b971aeb4c3664))

## v1.112.0 (2026-04-25)

### Features

* feat(product): per-product price-drop notification flag

Admins can now opt individual SKUs into the "Notify me when price
drops" feature on the PDP. Default off — existing products keep the
CTA hidden until an admin flips the flag.

Why: price-drop alerts make implicit promises. For products whose
pricing is volatile, manually managed, or runs frequent flash
discounts, every alert dispatch is noise. Gating per SKU lets the
business choose which products are stable enough to advertise the
feature on.

Changes:
- Product.price_drop_alerts_enabled BooleanField(default=False) +
  migration 0032.
- ProductDetailSerializer: new field exposed read-only (list payload
  unchanged — the CTA only renders on the PDP, no need to bloat
  search/listing rows).
- ProductAdmin: new "Customer Alerts" fieldset + list_filter for
  bulk toggling.
- product/views/alert.py::create(): when kind=price_drop and the
  product's flag is False, return 403 with a clear detail. Existing
  subscriptions keep working when an admin disables a previously-
  enabled SKU — only NEW subscriptions are blocked.
- 7 new integration tests in tests/integration/product/alert/
  covering: the gate (enabled vs disabled), the field on the detail
  payload, and that disabling doesn't break existing subscribers.
- schema.yml regenerated.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`cf30ecc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cf30eccf005a22c7e8f6ccb3ba137e4e3c558ad1))

## v1.111.0 (2026-04-25)

### Features

* feat: hardening pass — auth, tests, shipping docs, and stability

Backend hardening across multiple concerns plus the test-suite
stability work that took 4228 tests deterministic under -n auto.

Auth & login:
- Add argon2-cffi dependency so admin (Argon2-hashed) accounts can log in.
  Login was 500-ing with "No module named 'argon2'" until this lands.

Test-suite stability (-n auto / -n 4 / -n 12 deterministic):
- Switch to --dist loadfile in pyproject pytest addopts. Module-level
  singletons (translation_reload._local_translation_version,
  extra-settings cache, factory random state) were leaking between
  unrelated tests under worksteal.
- conftest: route django-extra-settings through DummyCache via direct
  monkey-patch of extra_settings.cache._get_cache (NOT a CACHES alias —
  resetting the caches registry breaks Channels middleware tests that
  patch cache._cache.get_client).
- conftest: drop statement_timeout / idle_in_transaction_session_timeout
  for tests; pytest-timeout=600 still bounds real hangs.
- conftest: disable psycopg pool for tests so conn.close() actually
  terminates Postgres sessions (otherwise TruncateTables stalls behind
  pooled async-test connections, leaking InvoiceCounter rows).
- conftest: close non-atomic DB connections after every test (was
  TransactionTestCase-only).
- Pin OrderFactory(status=PENDING, payment_status=PENDING) in
  test_mydata_submission helpers so EAGER signal cascades don't
  pollute mock_email before the test body runs.

Shipping (FedExCarrier / UPSCarrier):
- Replace stale "@TODO - Mock implementation" markers with proper
  class docstrings documenting the intentional stub (production routes
  through ELTA at the order level; carrier classes exist so the
  ShippingOption / get_tracking_info shape is in place when a real SDK
  ships). Add (stub) markers to logger.info calls so synthesised paths
  are visible at runtime.
- get_tracking_info logs now call out "always returns IN_TRANSIT" so
  the operational consequence (orders never auto-promote to DELIVERED
  via this path) is obvious in logs.

Schema regen + migrations:
- Order/SearchQuery user_agent column max-length migrations.
- schema.yml regenerated from spectacular.

Plus broader hardening across blog, cart, contact, core, meili,
notification, order, product, search, user (admin/serializers/signals/
views), see individual file diffs.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`df50a61`](https://github.com/vasilistotskas/grooveshop-django-api/commit/df50a617a30c47b576bd9cb098fc96320a2f01a1))

## v1.110.1 (2026-04-24)

### Bug fixes

* fix: uv lock update ([`0deaf60`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0deaf604146643c3ff0b37131d2aac0a0bc89ddd))

* fix(order): translate live cancel/refund notifications to Greek

On the Greek storefront, the WebSocket toasts fired by
``notify_order_status_changed_live`` and ``notify_order_refunded_live``
rendered as English source strings (e.g. "Order #34 canceled",
"Refund processed for order #34"), because:

1. The msgid literals in ``order/notifications.py`` were never
   wrapped with a gettext marker, so ``makemessages`` never extracted
   them into the .po file.
2. At runtime ``_render_translations`` calls ``_(title_msgid)`` which
   returns the input unchanged when the msgid is absent from the
   catalog — regardless of which locale is active.

Wraps every notification msgid (5 status-copy entries + 5 dedicated
live tasks — order placed / tracking / payment confirmed / payment
failed / refund processed) with ``gettext_noop`` so xgettext picks
them up, then fills in proper Greek translations.

Shipped end-to-end: Dockerfile already runs ``compilemessages`` at
build time (django-api/Dockerfile:48), so the new .mo lands in the
image and ``apply_db_overlay`` keeps the baseline as the fallthrough
when no Rosetta DB row exists.

Verified in prod ``deploy/backend`` via ``gettext()`` under
``override('el')``: all 3 target strings returned English before this
change; now they'll return proper Greek on next image deploy.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`3cd8623`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3cd8623f912b43559b41ef1fad1a861b164bd0bf))

## v1.110.0 (2026-04-23)

### Features

* feat(admin): gate B2B invoicing + fix GDPR export shared volume

Three independent admin/reliability fixes bundled together.

**B2B_INVOICING_ENABLED extra setting** lets the owner hide the
"Θέλω τιμολόγιο (Β2Β)" toggle on checkout from admin without a
deploy. Setting defaults to True. Whitelisted in PUBLIC_SETTING_KEYS
so the Nuxt checkout can read it anonymously.
OrderCreateFromCartSerializer.validate() now rejects
document_type=INVOICE when the setting is off — closes the direct-
API bypass that would otherwise defeat the UI gate. Regression test
covers all three branches (INVOICE off → reject, INVOICE on →
accept, RECEIPT → always accept).

**GDPR export PermissionError fix** — celery_worker was writing
exports under MEDIA_ROOT (/home/app/web/mediafiles), which doesn't
exist on the worker pod and whose parent is not writable by the
unprivileged app user. Moved to PRIVATE_MEDIA_ROOT/_gdpr_exports/
via new get_export_location() helper — same mediafiles_private PVC
(mode 777) that invoice PDFs already share between backend and
celery_worker. Download view reads from the same helper.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`86b6cd9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/86b6cd9d668804ffbdc7fe642906cbbece279476))

### Testing

* test(conftest): reseed extra_settings before every DB test

Fixes intermittent ``test_stock_reservation_ttl.py`` failures where
the configured TTL (30 min from ``EXTRA_SETTINGS_DEFAULTS``) was
replaced by the code-level fallback (15 min) under xdist parallel
runs.

Root cause: ``django-extra-settings`` seeds its ``Setting`` rows via
a ``post_migrate`` signal that only fires once at DB creation. Any
test marked ``@pytest.mark.django_db(transaction=True)`` (three in
the suite — concurrent stock tests + one stock manager case) flushes
all tables on teardown, wiping those rows. The next test's
``Setting.get("STOCK_RESERVATION_TTL_MINUTES", default=15)`` hits an
empty DB and falls back to the ``default=`` arg, returning 15 while
``StockManager.reserve_stock`` internally reads the (now-cached) 15
→ assertion ``created_at + 30min`` fails against ``created_at + 15min``.

``set_defaults_from_settings`` is a per-entry ``get_or_create``, so
the fixture is a no-op when seeds are intact and restorative only
after a transactional teardown. Verified with a previously-flaky
run of ttl + concurrent_stock_operations + concurrent_stock +
stock_manager + expired_reservations: 187/187 pass under ``-n auto``.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`0a9055a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0a9055a07d27e24b4ae577b63fb55296e3b8586c))

## v1.109.1 (2026-04-23)

### Bug fixes

* fix(schema): resolve documentType enum collision in OpenAPI

Order.document_type (6 values: full OrderDocumentTypeEnum) and
OrderCreateFromCartSerializer.document_type (2 values: RECEIPT|INVOICE)
both serialise as ``documentType``, so drf-spectacular was collapsing
them under an auto-generated ``DocumentType128Enum`` name that churned
on every regeneration and bled into the frontend types.

- Extract the creation-time subset into a dedicated
  ``OrderCreateDocumentTypeEnum`` TextChoices class so the two choice
  sets have distinct source identities.
- Wire the serializer to the new class (``choices=...`` + default).
- Add both enums to ``ENUM_NAME_OVERRIDES`` so they surface as the
  stable ``OrderDocumentType`` / ``OrderCreateDocumentType`` schemas.

``uv run python manage.py spectacular`` now runs with zero warnings.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`8543b10`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8543b103aa6b89468e8d4c442aee47d2d3c9d593))

## v1.109.0 (2026-04-23)

### Bug fixes

* fix(invoice admin): RangeDateFilter on issue_date (DateField, not DateTimeField)

Invoice list (/admin/order/invoice/) 500'd with
``TypeError: Class <class 'DateField'> is not supported for
RangeDateTimeFilter``. Invoice.issue_date is a DateField per
Greek tax law (no time component — only the fiscal date matters);
Unfold's RangeDateTimeFilter hard-rejects non-DateTime columns.
Switched to RangeDateFilter which is the Date-only variant.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`9db3bac`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9db3bac99f64f57169aba85aa170e67cba807ede))

### Features

* feat(mydata): Tier B — B2B invoiceType 1.1 with buyer ΑΦΜ

Live-verified against AADE dev: submitted a real 1.1 invoice and
got MARK 400001961706127 back with a working verification QR URL.
Tier A (11.1 retail) flow is unchanged — all 82 existing tests
plus 6 new Tier B regressions green.

Scope — domestic Greek B2B only:
- 1.1 (Τιμολόγιο Πώλησης) when Order.billing_vat_id is set and the
  counterpart is GR. Intra-EU (1.2) / third-country (1.3) remain
  out of scope per the agent research — they need reverse-charge
  VAT exemption handling and distinct classifications.
- The service layer short-circuits "document_type=INVOICE without a
  billing_vat_id" with a clear REJECTED state + error code so the
  admin surfaces the misconfiguration immediately (instead of
  silently falling back to 11.1, which would be tax-fraud-adjacent).

Data model (migration 0033, additive, blank defaults):
  Order.billing_vat_id CharField(12)
  Order.billing_country CharField(2)

Builder:
- _pick_invoice_type: 11.1 if no buyer VAT, 1.1 if VAT set + buyer
  country equals issuer country. Non-GR buyer with VAT falls back
  to 11.1 (service layer rejects upstream anyway).
- _emit_counterpart: vatNumber + country + branch only. No <name>
  for GR counterparts (AADE error 220 forbids it).
- _normalise_buyer_vat strips EL/GR prefix (VIES convention) so
  AADE doesn't reject with error 104 ("Invalid Greek VAT number").
- Classification switches from retail pair (E3_561_003 / category1_3)
  to wholesale (E3_561_001 / category1_1) based on invoiceType —
  error 313 ("classification forbidden for invoice type") otherwise.

Service layer:
- New MyDataInactiveCounterpartError subclass of MyDataValidationError.
  Fires on AADE error 102 ("Vat number is not active corporation")
  so the Celery task can queue a customer-facing "check your ΑΦΜ"
  email path in a follow-up, instead of a generic REJECTED notice.
- _guard_b2b_invoice_integrity pre-check: rejects
  document_type=INVOICE without billing_vat_id with a clear
  MISSING_BUYER_VAT code persisted on the Invoice row.

Serializer + OpenAPI:
- OrderCreateFromCartSerializer gains billing_vat_id +
  billing_country + document_type fields with validation:
  - Strip EL/GR prefix, enforce 9-digit Greek ΑΦΜ.
  - ISO-alpha2 country normalised to uppercase.
  - Cross-field: INVOICE ⇒ billing_vat_id required.
- schema.yml regenerated — the Nuxt side picks up billingVatId /
  billingCountry in the auto-generated zod + types.

Nuxt frontend:
- useCheckoutForm formState gains billingVatId + billingCountry;
  superRefine on step1Schema mirrors the Django 9-digit ΑΦΜ rule
  with an inline error in Greek ("Το ΑΦΜ πρέπει να έχει 9 ψηφία...").
- StepPersonalInfo.vue exposes a "Θέλω τιμολόγιο (Β2Β)" toggle that
  reveals the ΑΦΜ field; toggling off clears the field so stray
  values never reach the API.
- useCheckoutSubmit forwards billingVatId / billingCountry /
  documentType in the POST body.

Tests (6 new builder regressions + existing suite updated):
- 1.1 routing when billing_vat_id set
- 11.1 stays 11.1 for Tier A orders
- counterpart block present for 1.1 (vatNumber/country/branch, no name)
- counterpart absent for 11.1
- wholesale classification pair (E3_561_001 / category1_1) on 1.1
- EL/GR prefix stripped before emit

Pre-existing integration tests pin document_type=RECEIPT now so the
OrderFactory's random-doctype roll doesn't intermittently trip the
new Tier B guard.

SMTP dev fix (side-item, same commit because it surfaced during
Tier A UI testing): .env EMAIL_HOST changed from ``localhost`` to
``mailpit`` so the celery_worker container can reach the mailpit
SMTP service via its compose-network name. Verified: Django's
send_mail now lands in Mailpit's http://localhost:8025 UI.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`b9ee44e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b9ee44e624fd31d9d084caacda218cdf913973e3))

## v1.108.1 (2026-04-22)

### Bug fixes

* fix(invoicing): share mediafiles_private across containers + drop orphan PDFs

Two dev-parity fixes surfaced while verifying the myDATA chain end-to-
end with real AADE dev:

1. **``mediafiles_private`` now a shared named volume** across backend
   + celery_worker + celery_beat. Without it the worker regenerated
   the post-MARK PDF into its own container-local FS while the
   backend kept serving the pre-myDATA version — fine in prod
   (S3 is shared) but a broken experience in dev. Named volume
   mirrors the S3 behaviour.

2. **Force-regen deletes the stale PDF before writing the new one.**
   Django's ``FileSystemStorage.get_available_name`` (and ``S3Boto3Storage``
   with ``file_overwrite=False``) appends a random suffix when a file
   with the same name exists. That left every regeneration with an
   orphan ``invoice_number.pdf`` + the real ``invoice_number_<hash>.pdf``
   the DB pointed at — accumulating junk and costing an extra S3
   object per force=True call. Now ``generate_invoice`` deletes the
   old ``document_file`` before saving; the regenerated PDF lands
   at the same storage key every time.

Verified end-to-end: after this change, chain run #11 produced a
single ``INV-2026-000011.pdf`` (34kB) with MARK 400001961694800 +
AADE QR embedded; no orphan on disk.

Full suite 267/267.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`3e7a11d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3e7a11d715d1847464d07097530618ba73330300))

* fix(mydata): AADE dev roundtrip now succeeds end-to-end

Integration tests passed locally but the real AADE dev sandbox
rejected every submission. Live roundtrip revealed six bugs the
authoritative PDF didn't flag clearly. All fixed and pinned with
regression tests; a real invoice now posts successfully (MARK
400001961694181 returned on the final smoke run).

What broke and how it's fixed:

1. **Wrong payment codes in types.py** — every submission sent the
   wrong ``paymentMethodDetails.type``. AADE v1.0.10 annex 8.12
   numbers: 1=domestic bank, 2=foreign bank, 3=cash, 4=cheque,
   5=on credit, 6=web banking, 7=POS / e-POS, 8=IRIS. My constants
   had 3=web_banking, 4=POS, 7=cash (shifted by one). Fixed.

2. **Wrong XSD element order** — thought ``paymentMethods`` had to
   come BEFORE ``invoiceHeader`` (misreading the PDF's field-list
   table). AADE's live error response is the only authoritative
   source: ``issuer, counterpart, invoiceHeader, paymentMethods,
   invoiceDetails, taxesTotals, invoiceSummary``. Fixed.

3. **Missing AADE default namespace** — without
   ``xmlns="http://www.aade.gr/myDATA/invoice/v1.0"`` on the root,
   AADE returns error 101 for every element ("Could not find
   schema information"). Added.

4. **Classification children in wrong namespace** — the
   ``<incomeClassification>`` outer element stays in the invoice
   namespace but its children (``classificationType``,
   ``classificationCategory``, ``amount``) must sit under
   ``https://www.aade.gr/myDATA/incomeClassificaton/v1.0`` (yes,
   AADE's URI has a typo: "Classificaton" missing an ``i`` — keep it).
   Added _emit_income_classification helper that does both.

5. **Wrong classification pair for 11.1** — ``E3_561_001`` +
   ``category1_1`` is for B2B wholesale, not retail. AADE error
   313 ("forbidden for invoiceType Item11_1"). Correct pair for
   retail is ``E3_561_003`` + ``category1_3`` (Λιανικές — Ιδιωτική
   Πελατεία). Fixed.

6. **Strict VAT validation** — unknown rates used to silently
   fall through to vatCategory=7 and trigger AADE error 217
   downstream. Now raises ValueError in the builder; the service
   layer catches it and persists a readable error code so ops
   sees "Unsupported VAT rate 10.0%" in admin instead of a
   misleading AADE error.

Also closed out from the agent review:

- **MyDataDuplicateError retry loop**: AADE error 228 was in the
  same retry branch as transport errors → 5 wasted round-trips
  returning 228 every time. Now treated as terminal (logs loud,
  chains email, flags for manual reconciliation via
  RequestTransmittedDocs when Tier A.5 lands).
- **Empty ResponseDoc crash**: parser.first() raised IndexError
  on empty docs (rare AADE gateway fault) bypassing all error
  handling. Now returns a synthetic TechnicalError row.
- **XSD validation error uncaught**: XMLSchemaValidationError from
  the optional validator bypassed MyDataError taxonomy. Now wrapped
  and converted to MyDataValidationError so the invoice ends up
  REJECTED in admin, not zombied in SUBMITTED.
- **Summary/line rounding drift**: summary used the bucket-rounded
  invoice.subtotal/total_vat while lines were rounded independently
  — multi-item mixed-VAT orders hit AADE error 203/207-210. Now
  summary derives from the rounded line values accumulated during
  emit.
- **Shipping + payment_method_fee missing from XML**: caused
  paymentMethods.amount != totalGrossValue. Now emitted as extra
  invoiceDetails rows (VAT 24% — Greek standard for domestic
  shipping/fees; Tier B extends per export / island overrides).

New / updated tests (~10 regression tests total):
- payment codes: type=7 for card, type=3 for COD (was the inverse)
- element order: invoiceHeader precedes paymentMethods
- AADE namespace present on root
- incomeClassification present on every detail + aggregated summary
- classification children in the separate AADE namespace
- summary == rounded line sum
- unknown VAT rate raises ValueError
- 0% VAT emits vatExemptionCategory
- shipping + fee become detail lines so paymentMethods.amount
  matches totalGrossValue
- duplicate-uid error terminates immediately (not retried 5x)
- empty ResponseDoc returns synthetic error row (no IndexError)

Full suite 267/267 green.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`03c9f70`](https://github.com/vasilistotskas/grooveshop-django-api/commit/03c9f705d4f8ed5955d961de053edcd8be2d8089))

## v1.108.0 (2026-04-22)

### Bug fixes

* fix(mydata): ty diagnostics on client status narrowing + parser cast

ty's inference is stricter than mypy for two spots the first commit
slipped past:

client.py — ``requests.Response.status_code`` is typed ``int | None``;
chained comparisons (200 <= x < 300, x >= 500) don't narrow through
the Optional, so ty flags them as unsupported operators on None.
Coerce once to ``status = response.status_code or 0`` and use that
throughout the method.

parser.py — ``# type: ignore[arg-type]`` on the Literal narrowing
isn't honoured by ty. Replace with a dedicated ``_coerce_status``
helper that whitelists via ``get_args(StatusCode)`` and uses
``typing.cast`` for the final narrowing. More honest + testable
than the ignore anyway: unknown strings now deterministically fall
back to ``TechnicalError`` instead of being silently typed-as-correct.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`96daed1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/96daed177617520720bb56425f47c4af1fdcd03f))

* fix: update uv lock ([`045a1ed`](https://github.com/vasilistotskas/grooveshop-django-api/commit/045a1ed30feb401f05d881e61d168ed08d0f5ab0))

### Features

* feat(mydata): task wiring + admin actions + PDF MARK/QR + dashboard alerts

Completes Tier A — Celery chain submits invoices to AADE on order
completion, customer receives a PDF carrying the legal MARK + AADE-
returned QR code, admin can submit/cancel manually, dashboard flags
misconfiguration and rejections. Still feature-flagged via
MYDATA_ENABLED — flip it on after sandbox smoke test.

Task chain (order/tasks.py):
- send_invoice_to_mydata: new task. Runs the service-layer submit,
  classifies exceptions (Transport/Duplicate → retry, Validation/
  Auth → terminal), on success regenerates the PDF with force=True
  (so the customer's PDF embeds the MARK + AADE qr_url), then chains
  send_invoice_email. On terminal failure still chains the email so
  the customer isn't left empty-handed while ops reconciles.
- cancel_mydata_invoice: new task. Routes through service.cancel_invoice;
  retries on transport, gives up terminal, logs OrderHistory on success.
- generate_order_invoice now routes to send_invoice_to_mydata when
  MyDataConfig.is_ready() and auto_submit is on; otherwise retains
  the original direct-to-email path.

PDF (order/invoicing.py + template):
- _build_context now prefers invoice.mydata_qr_url over the
  order-tracking URL — the QR points at AADE's verification portal
  once the MARK is assigned (the legally-compliant scan target).
- Template renders a MARK line in the header meta when present. "MARK"
  translated to Μ.ΑΡ.Κ (el), kept as "MARK" (de).

Admin:
- InvoiceAdmin: mydata_status_badge column (colour-coded per state
  with the MARK number inlined when present), mydata_status in
  list_filter, mydata_mark/mydata_uid in search_fields, new "myDATA
  (AADE)" fieldset grouping the 13 integration fields as read-only.
- OrderAdmin: two new detail actions — "Send invoice to myDATA"
  (primary, cloud_upload icon) and "Cancel invoice in myDATA" (danger,
  cancel icon). Both dispatch the Celery tasks fire-and-forget with
  clear success / warning messages surfaced via the admin messages
  framework.

Dashboard banners (admin/dashboard.py + index.html):
- _check_mydata_state: fresh (un-cached) query of MYDATA_ENABLED +
  credentials + rejections in the last 7 days.
- Template renders three conditional banners: red when enabled but
  credentials missing, amber when recent rejections exist (deep-links
  to the filtered Invoice changelist), blue info when pointing at the
  dev sandbox. Fresh per page load — fixing the setting is visible
  on the next refresh.

Tests (9 new): test_mydata_submission.py covers the chain decision
(enabled vs disabled), success path (MARK + qr_url persisted, email
chained, PDF re-rendered with MARK), validation error path (REJECTED
state + email still sent with pre-transmission PDF), transport retry
fallback (max retries exhausted → fall back to email), cancellation
persists cancellation_mark, no-MARK cancellation skip. Full invoice
test suite remains green (256/256).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`d41b067`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d41b0678008f80bea4526e85594806491e309bc9))

* feat(mydata): foundation — models + builder + client + service (Tier A scaffold)

Scope: Greek IAPR / AADE myDATA integration for B2C retail sales
(invoiceType 11.1). Feature-flagged off — toggle MYDATA_ENABLED in
Settings admin after sandbox validation. Full submission + cancellation
flow lands end-to-end; Celery task wiring + admin UI + PDF template
changes follow in a separate commit.

New package order/mydata/:
- config.py: MyDataConfig snapshot resolved from extra_settings. Hard-
  coded dev/prod base URLs (AADE publishes them) so a typo in the env
  setting can't misroute prod traffic.
- uid.py: deterministic SHA-1(issuer_vat, issueDate, branch, invoiceType,
  series, aa, deviationType) over ISO-8859-7 per AADE v1.0.10 §5. This
  is the idempotency anchor — retries MUST hash identically so AADE
  dedupes via error 228.
- builder.py: Invoice → InvoicesDoc XML. 2dp ROUND_HALF_UP money,
  sum-then-round line totals (avoids errors 203/207–210), Greek text
  emitted as native UTF-8 (double-escape → schema rejection).
- client.py: requests.Session wrapper; classifies 401/403 as
  MyDataAuthError (not retryable), 429/5xx as MyDataTransportError
  (retryable). No retry policy here — that's the service/Celery layer.
- parser.py: ResponseDoc → typed ResponseRow. Namespace-tolerant (AADE
  ships both prefixed and bare); handles partial-success batches.
- validator.py: OPTIONAL XSD pre-validation. No-op until ops drops the
  official AADE XSDs into xsd/ (they're behind portal auth — we can't
  ship them). AADE validates server-side too, so this is fail-fast
  not correctness.
- service.py: public submit_invoice / cancel_invoice. select_for_update
  persists UID + identity fields BEFORE the HTTP call so transport
  failures leave recoverable state. Error 228 → MyDataDuplicateError
  (Tier A.5 will recover via RequestTransmittedDocs); all other
  ValidationError/XMLSyntaxError → REJECTED state + typed exception.
- exceptions.py: 4-class taxonomy driving retry policy (Transport vs
  Auth vs Validation vs Duplicate).
- types.py: AADE enum constants (invoiceType, paymentMethodDetails.type,
  vatCategory, known errorCodes) — no IntEnum wrapping since AADE
  ships plain ints/strings in the XSD.

Invoice model additions (migration 0032):
  mydata_status, mydata_invoice_type, mydata_series, mydata_aa,
  mydata_uid, mydata_mark (unique, bigint), mydata_authentication_code,
  mydata_qr_url, mydata_cancellation_mark, mydata_error_code,
  mydata_error_message, mydata_submitted_at, mydata_confirmed_at.
  All nullable/blank-default so pre-myDATA invoices migrate cleanly
  (the "leave existing invoices alone" policy). BTreeIndex on
  mydata_status for admin filters.

Settings (EXTRA_SETTINGS_DEFAULTS): MYDATA_ENABLED, MYDATA_AUTO_SUBMIT,
MYDATA_ENVIRONMENT (dev/prod), MYDATA_USER_ID, MYDATA_SUBSCRIPTION_KEY,
MYDATA_INVOICE_SERIES_PREFIX (default 'GRVP'), MYDATA_ISSUER_BRANCH
(default 0), MYDATA_REQUEST_TIMEOUT_SECONDS.

Dependencies: requests (already transitive, now explicit),
xmlschema==4.2.0 (only for the optional XSD validator).

Tests (19 new):
- test_uid.py (6): determinism, distinct-inputs-distinct-outputs, ISO-
  8859-7 encoding pin, B1/B2 receiver-VAT inclusion.
- test_parser.py (5): success, ValidationError, namespaced response,
  partial-success batch, cancellation mark.
- test_builder.py (8): InvoicesDoc root, issuer fields, header
  (invoiceType=11.1, series={prefix}-{year}, aa as plain int),
  line-sum == summary (errors 203/207–210), payment amount ==
  totalGrossValue, uid deterministic.

Authoritative source: myDATA API Documentation v1.0.10 (AADE
pre-official ERP, Nov 2024) — dropped in docs/ by the user.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`76a885d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/76a885d0ac8b442996693dc53d233375680fa75e))

* feat(invoicing): email PDF on completion + seller-unconfigured banner + misc polish

Email:
- New send_invoice_email task chained from generate_order_invoice once
  the PDF is ready. Attaches the rendered PDF (streamed from storage
  via storage.open('rb') — works on S3 and FS), renders under the
  buyer's language, and is idempotent via INVOICE_EMAIL_SENT_FLAG in
  Order.metadata (same pattern as the existing confirmation-email
  reservation). Templates at emails/order/invoice_issued.{txt,html}.

Admin banner:
- dashboard_callback now computes seller_config_warnings fresh on
  every load (not cached — fixing the setting should be reflected
  immediately) and flags empty INVOICE_SELLER_NAME / VAT_ID /
  TAX_OFFICE. The index.html template renders a red alert at the
  top with a "Fix in Settings" link that pre-filters the settings
  admin to the INVOICE_SELLER_ prefix. Non-critical fields (address,
  phone) don't trigger the warning to avoid nagging.

generate_invoice force=True preserves the original invoice_number
and issue_date — allocating a fresh counter slot on regeneration
was leaving gaps in the sequential register (Greek tax law forbids
gaps). Admin "Regenerate invoice" action message and variant updated
to reflect the new no-gap behaviour.

Doc drift:
- serializers_config['invoice'] description no longer says "short-lived
  signed URL" (it's now an absolute URL to a Django-gated endpoint).
- Invoice.document_file.help_text and order.invoicing module docstring
  describe the actual flow (Django-gated streaming, storage URLs as
  defence-in-depth).

Tests: +3 new (send_invoice_email attaches PDF, idempotency flag
prevents double-send, missing PDF skips & releases flag), existing
force-regeneration test tightened to assert invoice_number + issue_date
preservation and unchanged counter. Full invoice suite 52/52.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`92a61db`](https://github.com/vasilistotskas/grooveshop-django-api/commit/92a61db27ee8f73e744f9951b3336ba2b971b7ea))

## v1.107.0 (2026-04-22)

### Features

* feat(invoicing): streaming download endpoint + de locale + seller defaults

Customer-facing invoice download was silently broken in both modes:
- S3 prod: AWS_QUERYSTRING_AUTH=False global meant document_file.url
  returned unsigned S3 URLs that 403 without IAM.
- FS dev: url() returned /media/... which isn't served (private files
  live under mediafiles_private/).

New flow:
- Added OrderViewSet.invoice_download action (GET
  /api/v1/order/<id>/invoice/download) that streams the PDF via
  FileResponse + storage.open('rb'). Works identically on S3 and FS,
  gated by the same IsOwnerOrAdmin check as the metadata endpoint.
- InvoiceDownloadResponseSerializer.download_url now builds an
  absolute URL to that endpoint via request.build_absolute_uri —
  no raw storage URLs ever reach the client.
- PrivateMediaStorage sets querystring_auth=True so any future direct-
  storage consumer gets a presigned URL (defensive, not relied on).

extra_settings:
- EXTRA_SETTINGS_DEFAULTS gains 12 INVOICE_SELLER_* entries so the
  rows exist in the Settings admin for ops to fill in. Defaults are
  empty strings — a fresh install renders an obviously-incomplete
  invoice rather than one with a plausible-but-wrong legal identity.

German locale:
- Added 21 invoice-specific msgid/msgstr entries to de/django.po
  (RECHNUNG, USt-IdNr., Finanzamt, MwSt., etc.) and filled 4
  pre-existing empty entries (Payment, Description, Qty, Rate).
  English falls through to msgids, no en changes needed.

Cleanup:
- Dropped the dead InvoiceAlreadyExists class and its now-false
  docstring reference in generate_invoice.

Tests: 7 new — download URL points at the streaming endpoint, the
endpoint serves application/pdf, 404 when no invoice / no PDF, 403
for other users. Full suite still 13/13 in the invoice modules.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`47f9b0e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/47f9b0e88583c7669b0ff5c2b68347cbd755103b))

## v1.106.0 (2026-04-22)

### Features

* feat(invoicing): admin actions + QR/Greek-localised PDF

Admin:
- OrderAdmin gains "Generate invoice" (synchronous, idempotent) and
  "Regenerate invoice" (force=True, with counter-gap warning) detail
  actions, plus an InvoiceInline surfacing the current invoice on the
  order page. New InvoiceAdmin / InvoiceCounterAdmin give a read-mostly
  browser with per-row PDF download streamed via admin auth — works on
  every storage backend, not just S3.
- user/admin.py: adjust_loyalty_points used (self, request, queryset)
  while being declared in actions_detail. Unfold routes detail actions
  as <path:object_id>/..., so queryset was the user-id string; iterating
  it awarded points to each digit as a separate user. Fixed to the
  correct (self, request, object_id) signature.

PDF:
- Product name was rendering as the product ID — safe_translation_getter
  was called without the "name" arg, returned None, and |default:id fired.
  Fixed by resolving names in Python (_render_items) since Django
  templates can't pass kwargs to methods.
- Unified decimal formatting (locale-aware floatformat everywhere; VAT
  breakdown rows now render as Decimals, stored JSON still strings so
  the idempotency contract + tests stay intact).
- Unified VAT % format (floatformat:"-1" drops trailing ".0" only when
  the rate is integer).
- Added QR code (inline SVG via qrcode.image.svg.SvgPathImage — zero
  PIL on render path) pointing at the frontend order page.
- Greek compliance: tax_office (ΔΟΥ) + business_activity seller settings,
  buyer VAT ID field in the template (snapshot plumbing to follow).
- Shows order date when it differs from issue date.
- Pay-way display uses translated PayWay.name; payment_id falls under a
  "Ref:" label instead of being the primary payment line.
- Running footer with seller legal line on every page.
- Full Greek translation pass (.po updated, compilemessages run).

Notable gotcha caught during QA: Django's {% trans %} silently skips
msgids containing '%' — the trans tag post-processes as a format string
and a bare '%' swallows output. Keep '%' outside the tag.

dev.Dockerfile:
- Added gettext (msgfmt/xgettext) for makemessages/compilemessages.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`72593da`](https://github.com/vasilistotskas/grooveshop-django-api/commit/72593daa592fc36803020d4e9d99a0477d1009b1))

## v1.105.0 (2026-04-22)

### Bug fixes

* fix(invoicing): register humanize and update stale invoice task tests

Adds django.contrib.humanize to INSTALLED_APPS so the invoice template's
{% load humanize %} tag resolves. Rewrites two stale tests that asserted
NotImplementedError against generate_order_invoice — the task is now
fully implemented, so the tests verify successful PDF generation with
_render_pdf_bytes mocked out.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`0228ac8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0228ac85d91b595c09a351822cbe740d4f26739b))

* fix(auth): pin allauth user code format to 6-digit numeric

django-allauth 65.15.0 changed the default ALLAUTH_USER_CODE_FORMAT to
8-char dashed alphanumeric (RFC 8628), producing codes like SGKC-HSZJ,
while the Nuxt frontend's UPinInput is fixed at 6-digit numeric in both
the verify-email page and the login-by-code confirm form. Emails were
being sent with the new format and users could not complete signup.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`2a5cd33`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2a5cd33501318e612fa0c6369ee4569d2d97270c))

* fix(backend): plaintext notifications + per-action filterset dispatch

Two live-testing bugs fixed:

1. Notification messages leaked HTML (<a href='…'>name</a>) into the
   bell/page UI because the frontend renders them via text interpolation,
   not v-html. Dropped the anchor tags from every translation body in
   product.tasks (RESTOCK_FAVOURITE, PRICE_DROP_FAVOURITE) and
   blog.tasks (COMMENT_LIKED); navigation uses Notification.link, which
   the card wraps as a single tap target.

2. UserAccountViewSet's per-action filtersets (orders, favourite
   products, reviews, addresses, blog comments, liked posts,
   notifications, subscriptions) were *all* silently unapplied. django-
   filter's DjangoFilterBackend reads view.filterset_class via getattr,
   not by calling get_filterset_class(), so an override method bound
   nothing. Replaced with an _action_filter_map attribute and overrode
   filter_queryset() to materialise the correct class onto the instance
   before DRF's filter chain runs. Visible symptom: /account/notifications
   seen vs unseen tabs returned identical data.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`1c62f31`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1c62f3145053d60f44499ac83b75fc6d5e26402c))

* fix(order): allow null country/region on OrderSerializer, matching the model

``Order.country`` and ``Order.region`` are both ``on_delete=SET_NULL,
null=True, blank=True``. The serializer's ``PrimaryKeyRelatedField``
defaults to ``allow_null=False``, which made the generated OpenAPI
schema type them as required strings. Real orders with a null region
(e.g. imported from a legacy system with no region linked) then
tripped the Nuxt layer's Zod validation on ``parseDataAs(response,
zListMyOrdersResponse)`` with ``Invalid input: expected string,
received null``, turning the entire orders-list endpoint into a 500 on
the client side. Surfaced by the chrome-mcp smoke test of the
/account/orders page.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`85fc282`](https://github.com/vasilistotskas/grooveshop-django-api/commit/85fc28204bdabe2c5c287b380570d8ecd7ce99e4))

### Chores

* chore(dev): add mailpit to infra compose for local email testing

Runs a local SMTP server on :1025 with a web UI at :8025 so developers
can preview signup/verification/password-reset emails rendered as real
mail clients would decode them, instead of reading MIME-encoded output
from the console backend.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`d1cd163`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d1cd16398786cde78e36996c7711c79ce2a83d14))

### Features

* feat(gdpr): async data export + right-to-erasure with order retention

New UserDataExport model tracks async export jobs. A POST to
/user/account/{id}/request_data_export queues ``export_user_data_task``
which compiles every user-linked row into a JSON file and emails a
one-off 7-day download link (``data_export_ready`` template, rendered
under the user's preferred locale via translation.override). The
download endpoint is token-auth'd, not session-auth'd — recipients
open the link from email clients that may not have cookies.

Right-to-erasure POST to /user/account/{id}/delete_account requires
``{"confirmation": "DELETE"}`` then queues ``delete_user_account_task``.
The service anonymises Order rows (email, names, address, phone blanked
and user FK nulled) so tax/invoice retention survives; everything else
— product alerts, favourites, reviews, blog comments, Knox tokens,
allauth EmailAddress/SocialAccount/Authenticator/UserSession, the User
row itself — is hard-deleted inside a single transaction so a partial
failure rolls back to a consistent state.

Exports are written to MEDIA_ROOT/_gdpr_exports/ (bind-mounted, shared
between backend + celery_worker) rather than the sibling _private tree
the invoice pattern uses — the private tree is not volume-shared in
dev. Production deploys hitting S3 go through PrivateMediaStorage with
signed URLs; the download view is still single-scope to one token.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`80eb896`](https://github.com/vasilistotskas/grooveshop-django-api/commit/80eb8960cb47a1dc0e78797a22d1edb7017f3eff))

* feat(invoicing): PDF invoice generation with atomic per-year sequencing

- New Invoice + InvoiceCounter models. The counter hands out the next
  sequential number per fiscal year under ``select_for_update`` — Greek
  tax law forbids gaps and the counter is the only source of truth.
  Invoice stores the PDF in private media plus a frozen vat_breakdown,
  seller_snapshot, and buyer_snapshot so re-rendering an old invoice
  in a later year always yields the same VAT table even if product
  rates or the buyer's profile changed.
- order/invoicing.py service: _compute_vat_breakdown aggregates items
  by VAT rate (backs VAT out of gross prices so 24/13/6/0 buckets
  balance), _build_context isolates template data so tests can assert
  it without invoking WeasyPrint, and generate_invoice() is
  idempotent — calling twice returns the existing row.
- Seller info (AFM / registration / address) comes from
  extra_settings.Setting keys so ops can configure without a migration.
- core/templates/invoices/invoice.html — Greek-tax-compliant layout:
  seller + VAT ID header, buyer block, line items with per-item VAT%,
  per-rate VAT breakdown table, totals including shipping and payment
  method fee.
- generate_order_invoice task reimplemented to call the service;
  handle_order_completed now dispatches the task on transaction commit
  when document_type=INVOICE.
- GET /api/v1/order/{id}/invoice action returns InvoiceDownloadResponse
  (metadata + short-lived signed URL); 404s when the PDF has not been
  generated yet. has_invoice flag added to OrderDetail so the frontend
  can hide the download CTA without an extra round-trip.
- Dockerfile adds cairo/pango/gdk-pixbuf/libffi to both the builder
  (build-time) and the production runtime image (dlopen targets).
- pyproject.toml / uv.lock pick up weasyprint 68.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`ef3dd00`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ef3dd00340b3445373bf64e0b2002752364766ad))

* feat(notifications): add live notifications across order, payment, shipment, stock and loyalty events

- notification/services.py helper (create_user_notification) replaces
  ~30 LOC of boilerplate across blog and product tasks.
- NotificationTypeEnum catalogues all 14 fine-grained event identifiers
  so the frontend gets a typed union via OpenAPI — no more hardcoded
  "order_shipped" / "price_drop_favourite" strings.
- order/notifications.py covers order_created, status transitions
  (processing/shipped/delivered/completed/canceled), shipment
  dispatched, refund, and Stripe payment succeeded/failed — each
  dispatched via transaction.on_commit from its signal handler.
- New signals: order_shipment_dispatched (with pre/post-save caching of
  tracking fields; guards against duplicate fire when an admin clears
  and re-enters the same tracking number) and loyalty_tier_changed
  (with direction kwarg so downgrades stay silent).
- Back-in-stock live fan-out to product favouriters; complements the
  existing opt-in ProductAlert email path.
- OrderCancellationError now surfaces as 400 ValidationError so the
  frontend's conflict-aware retry UX triggers correctly.
- WebSocket payload carries id/category/priority/notification_type so
  the client can pick colours and icons without a re-fetch.
- NotificationUserSerializer split: detail includes nested Notification
  for list rendering; write path still takes integer FKs.
- /user/account/{id}/notifications action exposes the ``seen`` filter
  via @extend_schema so the generated Zod schema advertises it — no
  local schema overrides in the Nuxt layer.
- ENUM_NAME_OVERRIDES resolves the NotificationCategory vs
  SubscriptionTopic.TopicCategory collision that was renaming the
  latter to CategoryB9dEnum on every regeneration.
- Abandoned-cart email now links to /cart/recover/{cart.uuid} so the
  Nuxt side can show a welcome-back banner.
- Test fixture fix: NotificationStatusFilter lookups assert under
  translation.override("en") instead of the test env's default Greek.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`9989c46`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9989c46092724d861c531884742ea7226f7860fc))

### Testing

* test(order,notifications): tests + two regression fixes surfaced by them

Adds test coverage for the live-notifications + invoicing slices I
shipped without tests earlier. Two real bugs surfaced and are fixed as
part of this commit:

- **Loyalty signals referenced ``LoyaltyTier.level``** which doesn't
  exist — the model field is ``required_level``. Left uncaught, every
  tier transition would have crashed ``dispatch_tier_changed`` and
  silently never sent the celebratory notification. Writing the
  ``test_direction_up_fires_notification_task`` test was how this
  surfaced.
- **Dockerfile missing runtime fonts** — WeasyPrint's Pango backend
  needs actual TTF files on the filesystem. Without them the production
  image raised ``pango_font_describe: assertion 'font != NULL'``
  criticals and rendered empty PDFs. Added ``ttf-dejavu``,
  ``font-noto``, ``font-noto-cjk``, and ``fontconfig`` to the runtime
  layer. Verified end-to-end via
  ``docker run ... weasyprint ... Γειά σας`` — 6 KB valid PDF with
  ``%PDF-1.7`` magic.

New tests:
- ``tests/unit/notification/test_services.py`` — ``create_user_notification``
  contract (kind/category/priority/link propagation, unsupported-language
  skip, empty-copy skip, ``@transaction.atomic`` rollback on mid-loop
  translation failure, per-language fan-out).
- ``tests/unit/order/test_invoicing.py`` — ``InvoiceCounter.allocate``
  sequential + threaded-concurrent allocation (no duplicates, no gaps),
  ``_compute_vat_breakdown`` single/mixed/no-VAT buckets,
  ``_order_totals`` shipping + payment-fee addition,
  ``generate_invoice`` idempotency + force-refresh snapshot behaviour.
- ``tests/unit/order/test_shipment_signal.py`` — initial null→set dispatch,
  no-refire on re-save with same tracking (the ``tracking_unchanged``
  guard), refire after clear-then-set (legitimate new-shipment event),
  loyalty tier direction up/down/same gating.
- ``tests/integration/order/test_cancel_and_invoice_views.py`` —
  ``OrderCancellationError`` → 400 (not 500) contract, invoice endpoint
  404 when no PDF / row with no file, 200 with signed URL when PDF
  exists, other-user forbidden.

Other collateral:
- Added ``invoice`` and ``reorder`` to ``owner_or_admin_actions`` in
  ``OrderViewSet.get_permissions`` — without this, the new invoice
  endpoint fell to the ``IsAdminUser`` fallback and returned 403 for
  legitimate order owners.
- Ignored ``/mediafiles_private/`` (dev-time fallback for private
  storage when not using AWS).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`e933099`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e93309904ea8e70db93343369d9816b1bbdcd85f))

## v1.104.1 (2026-04-21)

### Bug fixes

* fix(filters): emit CSV-aware schema for camel-case ordering

DRF's ``OrderingFilter`` accepts a comma-separated list of keys (the
backend ``get_ordering_param`` already splits on ``,``), but the
spectacular extension for ``CamelCaseOrderingFilter`` emitted a
single-value ``enum`` — so every generated Zod consumer (openapi-ts)
rejected any multi-sort value. Concrete symptom: the checkout sent
``ordering=-isMain,-createdAt`` and Nuxt's Zod query validator
returned 400 before the request reached Django.

Replace the ``enum`` with a ``type: string, pattern: ...`` regex
that matches any comma-separated combination of the allowed camelCase
keys. ``re.escape`` guards against metacharacters so a future field
name like ``-createdAt`` doesn't corrupt the alternation. openapi-ts
regenerates as ``z.string().regex(...)`` — single-key sorts still
match trivially, multi-key sorts now validate, and the pattern keeps
the same allow-list protection the enum provided.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`6b9a2ab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6b9a2ab72933f90b14ebd33ac1361452e6914914))

## v1.104.0 (2026-04-20)

### Features

* feat(api): light up orphaned endpoints — address prefill, reorder, alert filters

- user/address: register the previously-orphaned ``get_main`` action so
  the checkout UI can prefill its form from the user's saved default
- order: register the ``reorder`` action so past orders can feed their
  items back into the active cart in one POST
- product/alert: expose ``product`` / ``kind`` / ``is_active`` via
  filterset_fields so the PDP can pre-answer "am I already subscribed?"
  and render the active state instead of a duplicate-submit path
- product/ProductAlertSerializer: map the ``EmailField(blank=True)`` to
  ``allow_null=True`` + normalise ``""`` to ``None`` in
  ``to_representation`` so round-trips aren't broken by Zod's
  email-format check over a default-empty string; drop the auto
  UniqueTogetherValidators that (as a side-effect) were forcing email
  to be required on input — DB-level UniqueConstraint + the view's
  IntegrityError → 409 already covers uniqueness
- search/listTrendingSearches: add concrete response serializers and
  typed query parameters so drf-spectacular emits a real schema instead
  of the graceful-fallback warning (0 errors now)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`bee8568`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bee8568556fb7019fc1788530256411908ad4306))

## v1.103.0 (2026-04-20)

### Features

* feat(hardening): cache admin dashboard, throttle cart/search, add correlation IDs

- admin: cache full dashboard_callback payload in Redis (5 min TTL),
  extract into _build_dashboard_data(), prefetch translations on
  top-products/low-stock/stock-log loops, and invalidate via
  post_save/post_delete signals on ten domain models
- throttling: new CartMutationThrottle, CartMutationAnonThrottle and
  SearchThrottle (layered on top of global daily caps) applied to
  CartViewSet/CartItemViewSet write actions and search_trending
- observability: CorrelationIdMiddleware + CorrelationIdFilter inject
  an X-Correlation-ID per request into log records (JSON + dev verbose)
- order: composite indexes on (user, -created_at) and
  (payment_status, -created_at) via migration 0030

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`4c99e04`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4c99e04b14651582babc50c05d7467ddd008b128))

## v1.102.0 (2026-04-20)

### Features

* feat: Bump Meilisearch ([`c976cdf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c976cdfedcd3bcaf8119f277517e52970732fd26))

## v1.101.0 (2026-04-19)

### Bug fixes

* fix: update uv lock ([`3878e38`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3878e3829d602fb47826a48b90959061986b5bb8))

### Features

* feat: quick-win feature batch — reorder, alerts, trending, idempotency

* order reorder: OrderService.reorder_to_cart + POST /orders/{id}/reorder
  clones past-order items into the user's cart, capping qty at current
  stock and returning added/skipped breakdowns so the frontend can
  surface partial-success toasts.
* product alerts: new ProductAlert model (RESTOCK + PRICE_DROP), guest
  or user subscriptions, single-shot fire-and-deactivate semantics.
  product_back_in_stock signal + notify_product_back_in_stock receiver
  dispatch Celery send_product_alert_restock on 0→positive transitions,
  and the existing price_lowered signal now also fans out to
  send_product_alert_price_drop for subscribers whose target_price
  threshold is met.
* trending searches: new /search/trending aggregates SearchQuery top
  queries over a 24h window, Redis-cached 5 minutes per
  (content_type, language_code, limit) tuple.
* idempotency: IdempotencyMiddleware replays cached 2xx/4xx JSON
  responses for retried POST/PUT/PATCH/DELETE when the client sends
  Idempotency-Key (RFC draft behavior, scoped by user/session, 24h).
* low-stock threshold: expose Product.low_stock_threshold on the
  product serializer so the frontend can show accurate "only N left"
  scarcity badges instead of a hardcoded threshold.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`a33f183`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a33f1833d78fe81c9c9b778e9d16e5384fa80fa9))

## v1.100.2 (2026-04-19)

### Bug fixes

* fix: lint ([`04c0c13`](https://github.com/vasilistotskas/grooveshop-django-api/commit/04c0c132d8420bfbe4fc0e53d952162c1a0aeda0))

* fix(throttle): add per-endpoint rate limits on contact and payment actions

Stacks ScopedRateThrottle-style subclasses on ContactCreateView (AllowAny)
and the create_payment_intent / create_checkout_session @actions so a single
IP/user cannot brute-force or abuse expensive payment provider calls past
5-10 requests/minute, while the global anon/user daily caps still apply.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`9ba370a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9ba370ad4c64f0739c476a3b41359fc9f888b252))

## v1.100.1 (2026-04-19)

### Bug fixes

* fix: update uv lock ([`01de097`](https://github.com/vasilistotskas/grooveshop-django-api/commit/01de09724117861e28515db86bb699a8291fe7fa))

## v1.100.0 (2026-04-19)

### Bug fixes

* fix: lint ([`989623f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/989623fcc9894f81ca73f1b0009f23cdff9d2f3b))

* fix(auth): override get_client_ip in adapter instead of trusting single header

The previous ALLAUTH_TRUSTED_CLIENT_IP_HEADER = "X-Real-IP" setting
made allauth's get_client_ip() read only that header and raise
PermissionDenied when missing. Combined with USERSESSIONS_TRACK_ACTIVITY
(which runs on every authenticated request via UserSessionsMiddleware),
any direct-to-Django caller without the header got 403 — health probes,
Celery-triggered HTTP, and the integration test suite all broke (17
analytics tests were failing with "Unable to determine client IP address").

Drop the setting and override UserAccountAdapter.get_client_ip to prefer
X-Real-IP (set by the Nuxt proxy from h3 getRequestIP) with a
REMOTE_ADDR fallback. Keeps the spoof-safe priority while staying
functional on proxy-less paths.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`3af4300`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3af4300d214b2866de1a5f49c793b2a4ab4e53ea))

* fix(auth): trust X-Real-IP for allauth session IP tracking

allauth 65.14.2 removed default X-Forwarded-For trust (anti-spoofing +
rate-limit-bypass protection), so get_client_ip() now falls back to
REMOTE_ADDR unless a trusted header or proxy count is configured. In
K8s that means UserSession.ip recorded the Nuxt pod cluster IP on
every request (USERSESSIONS_TRACK_ACTIVITY=True) instead of the real
client.

Nuxt sets X-Real-IP from h3 getRequestIP(event, { xForwardedFor: true })
and Django now reads it via ALLAUTH_TRUSTED_CLIENT_IP_HEADER.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`bd31805`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bd3180503340f8d674c5b57ab948a3ec3bcca678))

### Chores

* chore: sync uv.lock to pyproject version 1.99.1 ([`b10d3b8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b10d3b8d1259a4b1a5bd88f7811223fb134c92e0))

### Features

* feat(admin): revamp Unfold theme with oklch palette, dashboard badges, env banner

- Move SITE_TITLE/HEADER/SUBHEADER/SYMBOL to env-overridable strings,
  add SITE_URL, BORDER_RADIUS, SHOW_HISTORY/VIEW_ON_SITE/BACK_BUTTON,
  ENVIRONMENT callback, and LOGIN.redirect_after.
- Replace indigo RGB palette with zinc-based oklch base + primary scales
  and matching font semantic tokens.
- Add pending-review / pending-order / pending-comment / unread-message
  badges in the sidebar via admin/badges.py.
- Add admin/environment.py for the Unfold environment banner.
- Polish dashboard rating stars (5-of-10 display) and review status
  badges (NEW / TRUE / FALSE mapped to amber / emerald / rose).
- Sync static CSS + tailwind input to match new palette.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`0b82aa4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0b82aa40fc42f2e5c30ec75a848fdfd2f9a1b1c5))

### Unknown

* i18n(el): seed 41 missing msgstrs for order / cart / subscription / inactive-user email chrome

"Payment Status" arrived in English on a live test order even though
the operator had saved "Κατάσταση πληρωμής" via Rosetta. Two causes,
one fixed per-edit and one fixed at the baseline:

1. The Celery worker rendering the email may have been running an
   image that pre-dates the worker_process_init / task_prerun overlay
   wiring (f2498c71). Fresh worker pods on the latest image now pull
   DB-backed Rosetta edits on boot and refresh on the Redis version
   tick — if "Κατάσταση πληρωμής" is in core_translation, it WILL
   reach the next email.

2. The .po baseline had msgstr "" for ~40 strings across order_received,
   order_payment_confirmed, payment_failed, order_shipped,
   order_pending_reminder, checkout_abandoned, subscription/confirmation,
   and inactive_user templates. Seeded the obvious translations with
   a polib pass that only fills entries whose current msgstr is empty
   — any Rosetta edit already on disk is preserved (strict additive).

After deploy, even a worker that can't reach the Translation table at
boot (or with an empty overlay) serves Greek correctly from the .mo
fallback. The overlay still wins when it's present. ([`eb56a13`](https://github.com/vasilistotskas/grooveshop-django-api/commit/eb56a13ec6d4192bf669004de2cd77d6a1dd2f0a))

## v1.99.1 (2026-04-18)

### Bug fixes

* fix: update uv lock ([`446ec89`](https://github.com/vasilistotskas/grooveshop-django-api/commit/446ec896a74db31a4d92830035d4ad4f6fd43760))

* fix(i18n): make import_po_to_translations additive by default so Rosetta edits survive

The command's old behaviour was update_or_create for every row parsed
from .po, which silently overwrote every Rosetta edit with whatever was
committed in the image's .po file. PreSync doesn't call this command
today, but the ergonomics were a trap: any operator running it manually
after a makemessages run or merge conflict would wipe every translation
an editor had ever touched via Rosetta.

The DB Translation table is the durable source of truth (per the
overlay architecture in core/rosetta_storage.py). .po files are only
the schema seed. Align the command with that guarantee:

- Default is now additive: rows that already exist for (language_code,
  msgid, plural_index) are preserved; only missing msgids are imported.
- New --force flag restores the destructive behaviour for the rare case
  where the operator really does want to overwrite from disk (e.g.
  reseeding a freshly cloned DB from .po). Emits a prominent WARNING
  when set.
- Summary line now reports imported + preserved + skipped counts.

Tests cover all three paths: additive preserves Rosetta rows, additive
seeds missing msgids, and --force overwrites. 3/3 pass. ([`8ff2cb1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8ff2cb1c1fd6ac19de395ed52d00638bde171891))

* fix(i18n): translate email subjects for el + apply Translation overlay to Celery workers

Two bugs surfaced together when a live test order landed with
`Order Received - #38` in English despite a Greek msgstr existing in
Rosetta/Postgres:

1. .po msgids were fuzzy or empty for every transactional-email subject
   I added in the email overhaul. Django's gettext skips fuzzy entries
   unconditionally, so Rosetta saves that looked landed on disk never
   actually reached runtime lookups. Removed the fuzzy flag and wrote
   proper, non-fuzzy Greek translations for:

- Payment Confirmed - Order #{order_id}
- Order Received - #{order_id} (was fuzzy-carried from "Order Delivered")
- Payment Failed - Order #{order_id}
- Order #{order_id} Status Update - {status}
- Your Order #{order_id} Has Shipped
- Reminder: Complete Your Order #{order_id}
- Did you forget something? — {site_name}
- We miss you!
- Confirm your subscription (template heading)
- Confirm your subscription to {topic} (subject)
- Order Received (template heading; was fuzzy)

Verified via django.utils.translation.gettext under
translation.override('el') that each key resolves to the new msgstr.

2. Celery workers never applied the DB-backed translation overlay.
   core.middleware.translation_reload seeds and refreshes gettext
   catalogs on every web request, but workers never see middleware —
   so a long-lived worker serves whatever .po/.mo was baked into the
   deployed image and stays blind to every Rosetta edit until the pod
   is rolled. That's the architectural reason the Greek msgstr the
   operator had already saved in Rosetta never reached the email send.

Two new signal handlers in core/celery.py:
- worker_process_init → apply_db_overlay() once at boot, so fresh
  workers have DB-backed msgstrs in their in-memory catalog.
- task_prerun → compare TRANSLATION_VERSION_CACHE_KEY against a
  per-process counter; re-apply overlay + clear gettext caches when
  another pod bumped the tick. Mirrors the web-side middleware with
  the same single-Redis-round-trip cost per task.

On next deploy PreSync runs import_po_to_translations which seeds the DB
from these corrected .po msgstrs, bumps the version tick, and every web
pod refreshes on its next request; worker pods refresh on their next
task. Future Rosetta edits propagate the same way without a redeploy. ([`f2498c7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f2498c716b2e583709d3dd68fc2287593c0fe164))

## v1.99.0 (2026-04-18)

### Bug fixes

* fix(test): pin payment_status in email-sequence test to avoid factory randomness

OrderFactory.payment_status uses random.choice across every PaymentStatus
value (factories/order.py:180). When that randomly produced COMPLETED,
send_order_status_update_email's intentional dedup — skip the PROCESSING
email because the payment-confirmed email already fired — kicked in and
mock_email.send.call_count landed at 2 instead of 3.

Pin payment_status=PENDING in the test's OrderFactory.create to guarantee
the PROCESSING branch runs. ([`5c67883`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5c67883c328367986d419665f1efca3abeaca4fd))

* fix(test): update subscription tests for dedicated .txt render and API-based unsubscribe URL

- test_send_subscription_confirmation_success: send_subscription_confirmation
  now renders both .html and .txt (no more strip_tags fallback), so assert
  render_to_string was called twice with the expected template names.
- test_generate_unsubscribe_link: unsubscribe URL points at API_BASE_URL
  (the Django unsubscribe endpoint) not NUXT_BASE_URL, and no longer has a
  trailing slash. Rewrite the "no_site_url" case as "strips_trailing_slash"
  since API_BASE_URL is always set in real deployments and the old empty
  fallback produced an unreachable URL anyway. ([`3d19cd8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3d19cd8483434500c1fabb962e33ce160d752920))

* fix(schema): split UnsubscribeView into topic-scoped and all-topics variants

drf-spectacular was emitting "operationId unsubscribeViaLink has collisions"
and the same for unsubscribeOneClick because a single UnsubscribeView served
two URL paths (/<uid>/<token>/<slug> and /<uid>/<token>) — drf-spectacular
generates one operation per (path, method) pair but could only see one set
of @extend_schema decorators on the shared view.

Split into two thin classes so each URL has its own operation:
- UnsubscribeTopicView → unsubscribeFromTopicViaLink / …OneClick
- UnsubscribeAllView → unsubscribeFromAllViaLink / …OneClick

Shared logic lives in module-level helpers (_validate_unsubscribe_token,
_apply_unsubscribe, _unsubscribe_get_response, _unsubscribe_post_response)
so both classes stay thin and the DRF contract stays identical.

urls.py points at the two new classes via their existing route names so no
email URLs or external callers change.

Verified: `uv run python manage.py spectacular --file schema.yml` now emits
no warnings; schema.yml checked in. ([`25b07fe`](https://github.com/vasilistotskas/grooveshop-django-api/commit/25b07fe6dd26b17abbfab8e12c2866c79f64e7c2))

* fix: schema ([`5d6d131`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5d6d13140a8d3ee7bed583f2ff50171bbc4df4ea))

* fix(test): rename missed template_name to template_base in newsletter test

The send_newsletter() signature was renamed (template_name → template_base)
but one test still passed the old kwarg, which ty check flagged as an
unknown-argument. Matches the rename applied in the previous commit. ([`c1653c0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c1653c02d39a6f259ea5ef43ba35074682e73474))

* fix: remove order delivered email section ([`76afd27`](https://github.com/vasilistotskas/grooveshop-django-api/commit/76afd27ad36871e53feab3b10f5dcc72ef0a5125))

### Features

* feat(email): overhaul transactional email, add per-user language and one-click unsubscribe

Broken flows fixed:
- subscription confirmation template never rendered the {{ confirmation_url }} button, so opt-in was impossible; template now leads with the CTA.
- UserSubscriptionViewSet.confirm required IsOwnerOrAdmin but was reached from unauthenticated email clicks; new public ConfirmSubscriptionByTokenView at /api/v1/user/subscription/confirm/<token> uses the 64-char random token as sole auth.
- SUBSCRIPTION_CONFIRMATION_URL default pointed at example.com; now {API_BASE_URL}/api/v1/user/subscription/confirm/{token}.
- UnsubscribeView: added POST handler for RFC 8058 one-click compliance (returns 200 per spec) and made topic_slug optional so inactive-user / abandoned-cart links work without a topic.
- Newsletter management command referenced a non-existent template path; now points at emails/marketing/newsletter and renders both .html and .txt.
- Inactive-user task rendered HTML as plain text via send_mail(message=html_message=html); switched to EmailMultiAlternatives with a separate .txt render and a signed uid/token unsubscribe link (no more ?email= leak).
- Abandoned-cart email had no unsubscribe footer/header; added preferences link + tokenized unsubscribe URL.
- Contact signal sanitizes user-supplied name before it lands in Subject (CRLF injection guard).

Per-user language:
- UserAccount.language_code + Order.language_code fields (migrations 0023/0029); default settings.LANGUAGE_CODE.
- core.utils.i18n helpers: get_user_language, get_order_language, resolve_request_language (X-Language/X-Locale/Accept-Language).
- UserAccountAdapter.save_user + SocialAccountAdapter.save_user capture language on signup from the forwarded header.
- UserAccountAdapter.send_mail wraps allauth email rendering in translation.override(user.language_code) so verification/password-reset/MFA mails respect the stored preference.
- Every customer-facing Celery email task wraps subject + body rendering in translation.override(...): order confirmation, payment failed, status updates, shipping, pending-order reminders, checkout abandonment, inactive-user re-engagement, subscription confirmation, newsletter.
- UserWriteSerializer / UserDetailsSerializer expose language_code with validation against settings.LANGUAGES.

Email deliverability:
- List-Unsubscribe + List-Unsubscribe-Post: List-Unsubscribe=One-Click headers on newsletter, inactive-user, and abandoned-cart emails (RFC 8058).
- List-ID header per email category for Gmail grouping.
- reply_to=[INFO_EMAIL] set consistently.
- ACCOUNT_EMAIL_SUBJECT_PREFIX = "[{SITE_NAME}] " so every allauth mail is branded.

Background tasks and async:
- send_subscription_confirmation_email_task (3 retries, 300s backoff) replaces the sync util call from user.signals; dispatched via transaction.on_commit so the worker never reads a row before commit.
- Newsletter dedup via Redis SETNX key newsletter:sent:{slug}:{uid}:{date} with configurable window; manage command gained --force and --dedup-window.

Cleanup:
- Removed dead order/notifications.py EmailNotifier/OrderNotificationManager (replaced by order.tasks long ago); removed the dead test_notifications.py and the assert_not_called patch in test_signals.py.
- Updated inactive-user task tests to mock EmailMultiAlternatives (was mocking the old send_mail); updated newsletter tests for the template_name -> template_base rename.
- makemessages run for el/en/de. ([`45400bc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/45400bca826a8f729b82398f52b35b3af90cf8da))

## v1.98.0 (2026-04-18)

### Bug fixes

* fix: lint ([`eebf501`](https://github.com/vasilistotskas/grooveshop-django-api/commit/eebf5018e073367aca351d338b410375391dfe04))

* fix(i18n): stop hitting DB at import/boot time, bootstrap overlay via seed cmd

tests/unit/wsgi/test_*.py kept ERRORing with "Database access not
allowed, use the 'django_db' mark" on CI. Root cause:

wsgi/__init__.py line 20 invokes the full WSGI application at module
import time as a startup warmup (application({"PATH_INFO": "/"}, ...))

That runs the entire middleware stack, which during test collection
happens inside pytest-django's DB-access block. The previous design
had two places that could hit the DB during that warmup:

- CoreConfig.ready() → apply_db_overlay()
- TranslationReloadMiddleware.process_request → first-request overlay

Both were wrapped in try/except Exception, but pytest-django's
RuntimeError surfaced through the test collection anyway (likely via
logging or a secondary teardown path). Catching after the fact wasn't
enough — the only reliable fix is to not touch the DB during the
warmup.

Both hooks removed. Bootstrap flow is now:

1. PreSync: migrate (creates Translation table)
2. PreSync: uv run python manage.py import_po_to_translations
   — seeds the table AND bumps the shared Redis version tick
3. Pods boot; on first real request, middleware sees
   `_local_translation_version (None) != remote_version` and applies
   the overlay. Subsequent requests no-op.
4. Every Rosetta save re-bumps the tick via
   core.signals.rosetta.bump_translation_version_on_save.

Fresh cluster with no tick yet: middleware returns early, pod serves
the .mo msgstrs baked into the image until the first bump lands.
Acceptable starting state — the image already ships the committed
translations from the repo's .po files.

The management command now bumps the version on successful imports
(not on --dry-run), so both the first-deploy seed and any manual
reconciliation run propagate to running pods without restart.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`2934d3a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2934d3a9511e31a82ddb803ab4143a91d9586cdd))

* fix(i18n): preserve TranslationCatalog wrapper in DB overlay + harden tests

Three CI regressions from the prior feat(i18n) commit, all tied to the
shape of Django 6.0's in-memory translation catalog:

1. Admin actions calling ngettext (tag/product admin) blew up with
   "'dict' object has no attribute 'plural'". Django 6.0 wraps
   DjangoTranslation._catalog in a `TranslationCatalog` helper class
   (trans_real.py:73) rather than a plain dict — its `.plural(msgid, n)`
   method is used by `GNUTranslations.ngettext` (trans_real.py:278) to
   resolve plural forms across merged sub-catalogs. The previous
   implementation replaced `_catalog` with `dict(...)`, which is still
   subscriptable but loses `.plural`, so every ngettext call crashed.

apply_db_overlay now mutates the existing TranslationCatalog via its
`__setitem__` (which writes to `_catalogs[0]`) instead of replacing
it. Works identically for the plain-dict fallback on older Django
versions.

2. `tests/unit/core/test_translation_overlay.py::test_apply_db_overlay_
   tolerates_missing_translation_table` previously mocked the private
   `_overlay_rows` helper with `side_effect=ProgrammingError`, which
   bypassed the real helper's exception handler. Rewritten to patch
   `Translation.objects.filter` directly so the test actually exercises
   the swallow-and-return-[] branch inside `_overlay_rows`.

`_overlay_rows` also widened its `except` clause to catch `Exception`
rather than only `(OperationalError, ProgrammingError)` so pytest-
django's `RuntimeError("Database access not allowed, use the
django_db mark")` is handled gracefully in tests that don't have
the mark (unblocks tests/unit/wsgi/* collection errors).

3. `test_send_order_status_update_email_template_fallback` flaked
   after `fix(order): suppress duplicate status-update email on paid
   transitions` landed — OrderFactory's default payment_status is a
   random choice across every PaymentStatus value, so ~1 in 6 runs
   rolled COMPLETED, tripped the new "skip PROCESSING email when
   already paid" short-circuit, and never reached the template-
   fallback code the test was checking. Pinning `payment_status=
   PaymentStatus.PENDING` in `setUp` makes every test in that class
   exercise the intended email code path deterministically.

Also added a positive assertion in the singular-overlay test that
`catalog._catalog.plural` remains callable after the overlay, so any
future regression that swaps the TranslationCatalog wrapper for a
plain dict fails loudly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`f497ea0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f497ea03cd8c1133d61f5ab5da11227caa8c5efb))

* fix: lint ([`0c7a402`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0c7a402393fbf71d4429529ef6415442072982d4))

### Build system

* build(docker): compile gettext .mo at build time, untrack compiled catalogs

Added `gettext` to the builder stage's apk install (ships the
`msgfmt` binary Django's `compilemessages` needs) and a
`compilemessages --ignore=.venv` step after `uv sync`. The final
production image reads .mo files at runtime via Python's stdlib
`gettext`, which has no external dependency, so no apk install is
needed in the production stage — .mo files flow through the existing
builder→production COPY.

`*.mo` is now in `.gitignore` so the compiled catalogs don't diverge
from their `.po` sources between local edits and CI builds. The
three previously-tracked `.mo` files (de/el/en) were untracked in
the companion dedupe commit.

Eliminates a long-standing class of "msgids are in the .po but
translations aren't rendering" bugs caused by developers forgetting
to run compilemessages before committing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`e25ac18`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e25ac18930d58074f58b723ae36fcf7e36ffba6c))

### Chores

* chore(i18n): dedupe de/en .po files and refresh makemessages

A manually-appended email-template block at the tail of
locale/de/LC_MESSAGES/django.po and locale/en/LC_MESSAGES/django.po
introduced 11 duplicate msgid entries per file. msgmerge flagged them
as "fatal errors" and refused to extract new strings from code —
which is why de and en had been frozen for weeks while el kept
growing. scripts/dedupe_po.py walks each .po with polib, keeps the
entry whose msgstr is non-empty, and merges both entries' source
references so nothing is lost.

After dedup, makemessages -a picked up:
- 344+ msgids now extracted cleanly across all three locales
- "Payment Confirmed" / "Thank you! Your payment has been received…"
  / "Payment Confirmed - Order #{order_id}" now present in de + en too
- per-locale translated counts: el=116, de=73, en=73

The script is idempotent — re-running against a clean file does
nothing.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`903a1d3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/903a1d30801480ac2ad217dd79eaa23223b93f21))

### Features

* feat(i18n): make Rosetta view DB-only, no disk writes on save

Forced po_file_is_writable=False on DBBackedTranslationFormView so
Rosetta's save path never calls polib.POFile.save() to touch the
image-baked locale directory. That path would have failed on the
read-only image layer anyway, and in the prior shared-PVC setup it
raced with NFS cache invalidation. With this override:

- Rosetta uses its CacheRosettaStorage for the edit session only
  (Redis-backed, 24h working-copy cache).
- Our rosetta.signals.entry_changed receiver persists every edit
  directly to the Translation table — the durable source of truth.
- Our rosetta.signals.post_save receiver bumps the shared version
  key so other pods' TranslationReloadMiddleware re-applies the DB
  overlay within one request.

Net effect: the .po/.mo on disk is purely the msgid schema baked
from the image; msgstrs live and breathe in Postgres. The
grooveshop-backend-locale-pvc becomes redundant (kept for now as a
Stage 2 infrastructure cleanup — removing it prematurely would
conflict with rolling-deploy pods still mounting the PVC).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`6af1fcc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6af1fccbbf678702c178d9849cda8eb6b2726fe0))

* feat(i18n): database-backed Rosetta translations survive deploys

Rosetta edits previously lived on pod-local disk and were synced
across replicas via .po/.mo bytes in Redis with a 24 h TTL. Every
deploy overwrote /app/locale with the image, and anyone who saved a
translation ≥ 24 h ago lost their work. The cross-pod sync was also
fragile: a pod that booted with stale bytes could overwrite a fresh
image's .po mid-request.

This commit moves the source of truth to Postgres.

Architecture:

- `core.models.Translation` — language_code × msgid × plural_index with
  msgstr. Unique per (lang, msgid, plural_index). Plurals and
  pgettext contexts are represented natively (msgid_plural separate
  column, context embedded in msgid via \x04 like stdlib gettext).

- `core.rosetta_storage.apply_db_overlay(language_code=None)` reads
  the Translation table and mutates `trans_real.translation(lang)._catalog`
  in place. Atomic single-attribute rebind under the GIL; no lock
  needed. Handles both singular (string key) and plural (tuple key)
  gettext catalog entries.

- `core.signals.rosetta.persist_rosetta_entry_to_db` hooks the
  Rosetta `entry_changed` signal and upserts each edited msgstr
  (singular or plural form) into the Translation table.

- `core.signals.rosetta.bump_translation_version_on_save` keeps the
  existing Redis version-tick for cross-pod invalidation, but now
  calls `apply_db_overlay` + `_reload_translations` locally instead
  of reading .po bytes off disk/Redis.

- `TranslationReloadMiddleware` re-applies the DB overlay when the
  version key changes. The old `_sync_files_from_redis` disk-writer
  is gone — no more disk mutation at runtime.

- `CoreConfig.ready` calls `apply_db_overlay` at startup so every pod
  serves DB values from its first request. Guarded against
  OperationalError/ProgrammingError so `migrate` before the new
  table exists does not raise.

- `core.rosetta_views.DBBackedTranslationFormView` overrides
  Rosetta's `po_file` property so the editor form displays current
  DB msgstrs (and rehashes entries so POST round-trips still match).
  Registered on the same `rosetta-form` URL path before `rosetta.urls`
  is included — Django's first-match resolution picks it up.

- New command `import_po_to_translations` seeds the Translation table
  from whatever .po files are on disk (run once after migrate, or
  after a manual .po merge to reconcile).

Deploy flow (after this lands + `import_po_to_translations` runs
once):

1. New image boots with whatever .po is in the repo (msgid schema).
2. AppConfig.ready applies the DB overlay → in-memory catalog has
   correct msgstrs before first request.
3. Rosetta save → writes to disk + Redis version tick + Translation
   row upsert.
4. Other pods' middleware notices the tick, re-applies DB overlay.
5. Next deploy → image again carries only msgids; DB still wins.
   No more kubectl rescue.

Tests (9) cover: apply_db_overlay for singular + plural + empty
msgstr paths, persist signal for create/update/plural entries,
version bump integration, middleware's once-per-tick refresh logic,
and the migrate-phase guard when the Translation table doesn't
exist yet.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`a3828d2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a3828d230902d994d40293cb1c0047842d1fad16))

## v1.97.1 (2026-04-17)

### Bug fixes

* fix: update uv lock ([`1893579`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1893579e4c83c70965daf73d21ed7814be81f863))

* fix(order): suppress duplicate status-update email on paid transitions

When an online payment succeeded the customer received two emails in
quick succession:
  1. "Order #N Status Update - Σε επεξεργασία" (from the PROCESSING
     transition fired by OrderService.update_order_status)
  2. "Payment Confirmed" (from send_order_confirmation_email dispatched
     by the Stripe / Viva webhook handler)

Both carry the same "your payment went through, we're now processing
your order" message. The confirmation email is the authoritative one —
it has the itemised order summary, payment ID, and dedicated template.

send_order_status_update_email now short-circuits when new_status is
PROCESSING and payment_status is already COMPLETED (i.e. the PENDING →
PROCESSING transition was triggered by a paid webhook). The same logic
guards offline-to-paid admin transitions too.

For manual admin moves to PROCESSING on an unpaid order the status
email still fires, since payment_status won't be COMPLETED yet.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`64287d7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/64287d7db596c30406d075f45534a5acc879c5ff))

### Chores

* chore(i18n): merge production Rosetta edits with regenerated msgids

Pulled locale/el/LC_MESSAGES/django.po off a live backend pod
(backend-5fcb5c67c7-w24ts) and ran msgmerge --no-fuzzy-matching with
the prior makemessages output:

msgmerge prod-el.po locale/el/LC_MESSAGES/django.po -o merged-el.po

Result: 116 translated msgstrs — prod translations take priority,
new msgids from the audit (Payment Confirmed, Thank you! Your payment
has been received…, Payment Confirmed - Order #{order_id}, plus ~350
others) remain as empty msgstrs ready for Rosetta.

17 msgids that existed only in prod were dropped; 16 of them were
whitespace-only placeholders ("" / " " / " ") and the remaining one
used an obsolete %(site_name)s format no longer in the codebase.
Nothing meaningful lost.

This merge is a one-time rescue. The prod sync-via-Redis middleware
has a 24h TTL that races with deploys — see follow-up discussion for
a durable fix.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`f24ba9b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f24ba9b24ece592381802fb33a7d9e915336517f))

* chore(i18n): refresh Greek translations with makemessages

Ran django-admin makemessages -l el to pick up newly-added translatable
strings. The extraction surfaces 354 new msgids that weren't in the
.po file yet — most notably:
  - "Payment Confirmed"
  - "Thank you! Your payment has been received and your order is now
    being processed."
  - "Payment Confirmed - Order #{order_id}"

Without this refresh, the strings exist in the email templates and
code but are invisible to Rosetta, so translators can't add Greek
versions through the /rosetta/ admin UI.

Existing translations are preserved; this commit only adds empty
msgstr entries for new strings and refreshes source-location comments.
German and English .po files are untouched — de extraction currently
fails due to pre-existing duplicate msgid entries that belong in a
separate cleanup pass.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`582d33f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/582d33f6fd44573d9819ecbb29e87a567be54c48))

## v1.97.0 (2026-04-17)

### Bug fixes

* fix: update uv lock ([`6d832b3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6d832b3aff9f648e91e95d3eada76e074a3752bf))

* fix: correctness and performance audit fixes across signals, tasks, and Meili

- add dispatch_uid to every @receiver to prevent duplicate handler firing
  if an app module is re-imported (tests, shell reloads, WSGI workers)
- product.post_create_historical_record_callback scoped to
  Product.history.model so other audited models don't dispatch wasted work
- product.signals async reindex paths fetch pks via values_list instead of
  running .exists() followed by a full instance iteration — one query,
  not two
- notification.handle_notification_created caches serialized translations
  in Django cache for 60s so a fanout to N users issues one translation
  SELECT instead of N
- 5 bare @shared_task decorators in order.tasks gain autoretry_for,
  max_retries, retry_backoff, retry_jitter (check_pending_orders,
  update_order_statuses_from_shipping, cleanup_expired_stock_reservations,
  auto_cancel_stuck_pending_orders, send_checkout_abandonment_emails)
- order.auto_cancel_stuck_pending_orders wraps each cancel in
  transaction.atomic + select_for_update(skip_locked) so concurrent beat
  workers can't both cancel the same order
- order.tasks + core.tasks email subjects use gettext (eager) over
  gettext_lazy so resolution timing is deterministic at the call site
- meili.reindex_model_task adds order_by("pk") before batch slicing —
  PostgreSQL slicing on an unordered queryset can skip or duplicate rows
- meili._client.wait_for_task accepts optional timeout_in_ms; the per-
  document indexing task uses 5000ms so Celery workers aren't held
  indefinitely when Meili queues are backlogged
- settings: DJSTRIPE_WEBHOOK_SECRET raises ImproperlyConfigured in
  production if the placeholder leaks through — prevents silent signature
  verification against a known literal
- settings: pin STRIPE_API_VERSION (not DJSTRIPE_-prefixed per dj-stripe
docs) so library upgrades can't silently shift webhook payload shapes

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`171f00e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/171f00ed42fc503f545d4adb10feee8d472ea75f))

### Features

* feat(order): SSE payment-status events via Redis pub/sub

Payment status updates are now pushed to subscribers instead of polled.
A new helper (order.payment_events.publish_payment_status) emits to
Redis channel payment:status:{order_id} after transaction.on_commit, so
downstream subscribers never read a pre-commit snapshot.

Wired into every authoritative payment-status transition:
- OrderService.handle_payment_succeeded / handle_payment_failed
- Stripe checkout.session.completed webhook
- Viva Wallet _handle_payment_succeeded / _handle_payment_failed

The Nuxt side subscribes via h3 createEventStream and forwards messages
to the browser as SSE, replacing the 10-attempt × 2s poll loop
(pollPaymentStatus) in the checkout flow.

Publish failures are swallowed — the payment state itself is already
in Postgres, and the Nuxt client falls back to polling if the stream
drops.

Tests cover channel format, payload shape (orderId, orderUuid, status,
paymentStatus, paymentId), and the Redis-down error path.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`766ad27`](https://github.com/vasilistotskas/grooveshop-django-api/commit/766ad270f68e6b74cb26687e0f2c6a35b34235ee))

* feat(auth): ticket-based WebSocket authentication

Knox tokens previously rode in the WebSocket URL as access_token query
params. URL strings are logged by every proxy, CDN, and browser history —
shipping a 7-day credential through that surface was unsafe.

New flow:
- POST /api/v1/websocket/ticket mints a single-use ticket (secrets.token_urlsafe)
  stored in Redis with 60s TTL, keyed to request.user.pk
- core.middleware.channels.TokenAuthMiddleware now prefers ?ticket=<value>
  on /ws/ URLs; the consumer middleware deletes the key on first read,
  so an intercepted ticket becomes useless after one use or 60s
- Legacy ?access_token= path kept as fallback for the migration window;
  remove it once all clients are shipping tickets

Tickets are authorization-only — the Knox token still lives in the
encrypted Nuxt session cookie for HTTP auth.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`2c45906`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2c45906ce9b653e840ad11ea24f355555d3179a8))

### Testing

* test: align signal disconnects with dispatch_uid and fix lazy-Redis patch

CI picked up two regressions from the prior audit commits:

- tests/integration/user/test_signals.py connected/disconnected
  create_default_subscriptions without dispatch_uid, so after the
  audit added dispatch_uid="user.create_default_subscriptions" to the
  receiver, the disconnect() calls silently no-opped. The signal kept
  firing during the test body and the IntegrityError surfaced from
  double-creating the default subscriptions. Pass dispatch_uid on all
  three connect/disconnect pairs so the registry keys match.

- tests/unit/order/test_payment_events.py patched Redis as an
  attribute of order.payment_events, but Redis is imported lazily
  inside _publish (it never becomes a module attribute). Patch redis.Redis
  at the source instead. Error-path test patches redis.Redis.from_url
  directly.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`85c0e90`](https://github.com/vasilistotskas/grooveshop-django-api/commit/85c0e90325ec80d8a3da570bd91ffcffd4091ce9))

## v1.96.0 (2026-04-17)

### Bug fixes

* fix(order): close retry-payment race + quiet stale-cancel reservation logs

Three fixes from a follow-up review pass on the stock/payment hardening:

1. **Retry-payment race** — wrap the new-intent write in
   `transaction.atomic() + select_for_update()`. A late
   `payment_intent.payment_failed` webhook delivery for the OLD intent
   could otherwise land in the ~50ms window between our read and save,
   flip `payment_status` to FAILED, and enqueue a second failure email
   concurrently with the retry flow.
2. **Failed-webhook event-level idempotency** — mirror the guard
   already present on `payment_intent.succeeded`: store
   `metadata["webhook_processed_<event_id>"]` inside an atomic
   `select_for_update` check so Stripe redeliveries of the same failed
   event cannot double-fire the failure email.
3. **Stale-cancel log noise** — `auto_cancel_stuck_pending_orders`
   targets orders that are 30 min to 24 h old, by which point the
   5-minute `cleanup_expired_stock_reservations` task has already
   flipped their reservations to `consumed=True`.
   `cancel_order()` now catches `StockReservationError("already
   consumed")` at DEBUG level instead of emitting a warning per
   reservation for every legitimate stale cancellation.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`43b7a1b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/43b7a1bc7f78c633275bd947c0219f0077b05fd4))

### Features

* feat(order, product): stock safety nets, retry payment, and stock-history admin view

Broad hardening pass on the order/payment/stock pipeline plus operator
tooling. Every new moving part is idempotent via row-locked metadata
flags or per-model booleans so concurrent webhook deliveries, Celery
retries, and HA beat workers cannot emit duplicates.

Order + payment
- POST /order/{id}/retry-payment mints a fresh Stripe PaymentIntent on
  an order whose payment previously failed, preserving stock already
  decremented at order creation. Clears confirmation/failed-email
  idempotency flags so the new attempt can re-notify on success/failure.
  Guests are allowed via UUID.
- Payment-failed emails now trigger from both Stripe
  payment_intent.payment_failed and Viva Wallet's failed-event webhook,
  with a row-locked reserve-before-send pattern that prevents the
  double-delivery exposed by the code review.
- Confirmation email picks between order_received.html (offline, with
  pay_way.instructions) and order_payment_confirmed.html (online post-
  payment) based on pay_way.is_online_payment + payment_status.

Scheduled cleanup
- auto_cancel_stuck_pending_orders (every 15 min) cancels PENDING
  orders with FAILED payment older than 30 min, and PENDING orders
  older than 24h — but only for pay_way.is_online_payment=True, so
  COD/bank transfer orders are left alone. Delegates to
  OrderService.cancel_order which already restores stock atomically.
- check_low_stock_products (hourly) emits a single consolidated admin
  alert for products at/under the per-product low_stock_threshold,
  claiming rows under select_for_update(skip_locked=True) before
  sending and auto-clearing the flag when stock recovers.
- send_checkout_abandonment_emails (daily) emails authenticated
  customers whose StockReservations expired without a sale, gated by
  the new StockReservation.abandonment_notified flag. The query
  deliberately ignores `consumed` because the 5-minute cleanup task
  flips it before this task runs.

Configuration
- STOCK_RESERVATION_TTL_MINUTES default raised 15 → 30 to reduce
  false-expiries on slow checkouts.
- New extra_settings: ORDER_AUTO_CANCEL_FAILED_PAYMENT_MINUTES (30),
  ORDER_AUTO_CANCEL_PENDING_HOURS (24), LOW_STOCK_THRESHOLD (10),
  CHECKOUT_ABANDONMENT_HOURS (2).

Stock history admin
- New per-product view at /admin/product/product/{id}/stock-history/
  renders a Chart.js stacked bar chart (7/30/60/90/180/365 day
  windows) of daily StockLog activity, split by
  RESERVE/RELEASE/DECREMENT/INCREMENT, plus a 50-row recent-activity
  table. Linked from the stock_reservation_summary card on the
  product change page.

Schema
- Product gains low_stock_threshold + low_stock_alert_sent (plus the
  auto-generated HistoricalProduct mirror).
- StockReservation gains abandonment_notified.
- Both migrations are additive with DB defaults; safe under the
  project's PreSync-hook migration ordering.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`3f78d43`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3f78d4328717b249209ed5a9d6d1de30a00aca8d))

### Testing

* test(order): unpin TTL tests from 15 min + mock email task in failed-webhook history test

Two CI regressions from the stock/email hardening pass:

1. test_stock_reservation_ttl.py hard-coded 15-minute TTL assertions
   against the new STOCK_RESERVATION_TTL_MINUTES default of 30.
   Replace the literal `15` with a pytest fixture that reads
   StockManager.get_reservation_ttl_minutes() at test time, so tuning
   the extra-setting does not require edits here. The final
   "constant-backed" test now checks type/positivity rather than an
   exact value.
2. test_failed_payment_logs_order_history asserted exactly one new
   OrderHistory entry, but send_payment_failed_email.delay now runs
   eagerly under CELERY_TASK_ALWAYS_EAGER and adds its own "email
   sent" note — pushing the count to +2. Patch the email task in that
test so the assertion only measures the webhook's own entry
   (same fix previously applied to the sibling succeeded test).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`4006c89`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4006c894c6bd48a69d11846815a2a0bb42922533))

## v1.95.0 (2026-04-17)

### Bug fixes

* fix(order): isolate email task in webhook history test

test_successful_payment_logs_order_history asserts exactly one new
OrderHistory entry after the Stripe webhook runs, but the new
send_order_confirmation_email.delay call now fires eagerly under
CELERY_TASK_ALWAYS_EAGER and adds its own "email sent" note, pushing
the count to +2. Patch the email task in this test so the assertion
only measures the webhook's own history entry.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`25815bc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/25815bc30a8d8a7d2ee41a2dc676bb09ed18c9c0))

### Features

* feat(order): defer confirmation email until payment succeeds for online payments

Previously the order confirmation email was sent unconditionally on order
creation, which meant customers paying with Stripe or Viva Wallet received
the email before their payment had actually cleared. Offline methods (COD,
bank transfer) still get the email immediately since the customer needs
order details to pay manually.

- order_created signal now only enqueues the email when pay_way is offline,
  missing, or the order is already paid at creation time
- Stripe payment_intent.succeeded and checkout.session.completed webhooks
  now enqueue the email after mark_as_paid; checkout.session.completed
  uses transaction.on_commit so a rolled-back handler cannot send
- Viva Wallet _handle_payment_created enqueues the email after the
  transaction is verified as completed
- send_order_confirmation_email is now idempotent via a metadata
  reservation (confirmation_email_sent flag + select_for_update), so
  overlapping triggers (e.g. payment_intent.succeeded + checkout.session.completed)
  or retried webhooks cannot cause duplicate sends; the flag is released
  on permanent failure so admins can resend
- Tests updated to cover offline-immediate, online-deferred, idempotency,
  and webhook-triggered email paths

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com> ([`e8d10df`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e8d10dfe242c85538596eadc12ab10a8f95c6e10))

## v1.94.0 (2026-04-17)

### Features

* feat: Bump Versions ([`a512f93`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a512f9315ab3b61deb0914dbeef605c6de5bdb32))

## v1.93.8 (2026-04-14)

### Bug fixes

* fix: update uv lock ([`7174597`](https://github.com/vasilistotskas/grooveshop-django-api/commit/717459749bce83d7984fc8dda9c0d550d2160f38))

* fix: rosetta ([`503c22b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/503c22b5b0762eca8c0ff69e135d882e2d08552a))

## v1.93.7 (2026-04-14)

### Bug fixes

* fix: update uv lock ([`5f907b9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5f907b99cc8da5e879c3e3b89c55f22816c7f970))

* fix(viva): make IP check non-blocking + extensive debug logging

The IP whitelist hard-rejected webhooks because behind K3s Traefik with
externalTrafficPolicy=Cluster, the source IP is SNAT-ed to a node/pod IP
(10.42.x.x) and the original Viva IP is lost. Every real webhook was being
rejected with 403, payment status never updated.

Changes:
- _verify_source_ip → _check_source_ip: returns (matches, observed_ip)
  as informational signal. Does NOT block the request.
- _handle_webhook_event: log IP outcome but always proceed. The Retrieve
  Transaction API call (which uses our OAuth2 credentials and confirms
  the transaction exists in Viva's system) is the real authentication.
- Try every entry in X-Forwarded-For chain (not just rightmost), since
  proxies can SNAT at multiple levels.

Extensive debug logging added throughout the webhook flow:
- Request headers (REMOTE_ADDR, X-Forwarded-For, X-Real-IP, host, etc.)
- Full webhook payload
- IP check result (matched/observed)
- Order lookup (matched order id, current status)
- Idempotency hits
- Event dispatch
- Retrieve Transaction API call args + result
- Payment update success
- GET verification flow

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`b501a02`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b501a027266dae0375ad41e4ea783200f3a0c313))

## v1.93.6 (2026-04-14)

### Bug fixes

* fix: update uv lock ([`5a07428`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5a0742896ceff47acae9929c2f3e0644e0e15dfc))

* fix(viva): audit fixes — scoped token cache, accurate audit history, safer reversal

- viva_webhook: H1 — _handle_reversal_created now persists status_updated_at
  (was mutating in memory but missing from update_fields)
- viva_webhook: H2/H3 — _handle_payment_failed and _handle_payment_created
  capture previous_payment_status before mutating so OrderHistory records
  the actual prior state instead of hardcoded "pending"
- payment: M2 — scope token cache key by live/demo mode to prevent demo
  tokens leaking into live API calls (or vice versa) after mode switch
- payment: M3 — remove dead viva_order_code key from create_checkout_session
  return dict (was never consumed by caller)
- order.views.order.create_checkout_session: log warning when an existing
  viva_order_code is replaced (aids debugging retry/duplicate sessions)

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`8a13169`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8a1316943a05a95ba6b4350652d8d9d64f26ee2b))

## v1.93.5 (2026-04-14)

### Bug fixes

* fix: update uv.lock ([`164321f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/164321f554d80e52b7b9b884a5636616a990b4c6))

* fix(viva): replace broken HMAC with IP whitelisting, add StatusId + verification guards

Viva dashboard webhooks do NOT use HMAC signing — the old code checked
for X-Viva-Signature that Viva never sends, rejecting every webhook
with 403. Payment status never updated from "Pending".

Per https://developer.viva.com/webhooks-for-payments/:
- Replace _verify_hmac_signature with _verify_source_ip (IP whitelisting
  against Viva's published production/demo IP ranges)
- Add StatusId == "F" check from webhook payload before processing
- Require TransactionId on event 1796 — reject events without it to
  prevent unverified payment completions
- On Retrieve Transaction API failure: raise RuntimeError to roll back
  the event_key and return 500, so Viva retries the webhook fresh
- Wrap handler in try/except RuntimeError for the retry-on-failure flow
- Remove unused VIVA_WALLET_WEBHOOK_SECRET setting and hashlib/hmac imports

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`8f3b593`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8f3b59358bd7bef790fc48c13fbd0b4c5db4f238))

### Documentation

* docs: update CLAUDE.md with permission, stock, and Celery task changes

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`be78210`](https://github.com/vasilistotskas/grooveshop-django-api/commit/be782100c0ac52652af2990836063265d2a3f2b0))

## v1.93.4 (2026-04-13)

### Bug fixes

* fix: update uv lock ([`cda90d0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cda90d0c290d2927620769a3aacdeac95f6fc4f8))

* fix(cache): remove @cache_methods from BlogCommentViewSet (staff data leak)

BlogCommentViewSet cached list/retrieve responses but its get_queryset
branches on is_staff — staff users see unapproved comments while non-staff
only see approved ones. The URL-keyed cache served whichever response was
cached first to all users, leaking unapproved comments to non-staff.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`40173f8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/40173f83b5d72b9a4660beed945ac74785689bec))

## v1.93.3 (2026-04-13)

### Bug fixes

* fix: generate schema ([`4f27a6a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4f27a6a676f97bf083fdf22150f5bae7eab77821))

## v1.93.2 (2026-04-13)

### Bug fixes

* fix(security): resolve critical cache leaks, permission gaps, race conditions, and signal performance

- Remove @cache_methods from user-scoped viewsets (Order, Address, Notification, OrderItem)
  preventing cross-user data leakage via URL-keyed cache
- Add user-scoped get_queryset to NotificationUserViewSet
- Add permission_classes to BlogTagViewSet and ProductImageViewSet (admin for writes)
- Fix IsOwnerOrAdminOrGuest to require UUID verification for guest order access
- Change DRF default permission from AllowAny to IsAuthenticatedOrReadOnly
- Fix Stripe webhook TOCTOU with select_for_update + transaction.atomic (both handlers)
- Fix stock race condition in legacy create_order path
- Fix username change race with IntegrityError handling
- Bulk-optimize cleanup_expired_reservations (N+1 → 2 queries)
- Move price-drop, blog comment, and search analytics to Celery tasks
- Migrate loyalty tasks to MonitoredTask with autoretry
- Fix SMSNotifier stubs to return False, carrier-aware tracking URLs
- Fix SearchAnalyticsMiddleware IP spoofing, deprecated extra() in admin
- Fix Viva webhook missing paid_amount_currency in update_fields
- Add explicit AllowAny to cart viewsets for guest checkout
- Fix health_check to execute SQL, consolidate order status tracking

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`a5eb218`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a5eb2188982767b9a420ea510586d64ad77c0098))

* fix(email): prevent duplicate and unlimited order reminder emails

- Fix daily pending order reminder spam: add reminder_count and
  last_reminder_sent_at tracking to Order model, cap at 3 reminders
  with escalating intervals (1, 3, 7 days) via django-extra-settings
- Fix duplicate emails on SHIPPED (3→1): consolidate to single email
  path through handle_order_status_changed signal, remove redundant
  sync notifications.py calls and async task from handle_order_shipped
- Fix duplicate emails on DELIVERED (2-3→1): remove sync notification
  from handle_order_delivered, remove explicit email from shipping task
- Fix duplicate emails on CANCELED (2→1): remove sync notification
  from handle_order_canceled
- Fix monthly inactive user "We miss you!" spam: add
  reengagement_email_count and last_reengagement_email_at to
  UserAccount, cap at 3 emails with 90-day cooldown
- Move hardcoded task constants to django-extra-settings for runtime
  configuration: reminder limits, cart cleanup days, search limits,
  reengagement thresholds, notification expiration
- Fix flaky concurrent stock tests: add connection.close() in finally
  blocks, classify DB pool exhaustion errors separately from business
  logic failures

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`1d5a1cb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1d5a1cb8770278619752b59c356a5f72e2e9f5f7))

## v1.93.1 (2026-04-10)

### Bug fixes

* fix(email): revert item price formatting in sample data generator

The formatting loop added in the previous commit converted Decimal item
prices to formatted strings, breaking 3 unit tests that assert item
prices are Decimal values. Item prices must stay as Decimals since tests
and the total calculation depend on numeric types.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`5c301a6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5c301a63396c1916012b8303e4e19f74e2e15aca))

* fix: update uv lock ([`e2ed438`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e2ed4388eb7ea8f4ae1485400ee345cbde5d00ba))

* fix(email): use correct Django settings for site name, URLs, and email context

SITE_NAME and SITE_URL were never defined in settings.py, so every email
task/notification always fell back to "Our Shop" and "https://example.com".
This meant all customer-facing emails had wrong branding and broken links.

- Add SITE_NAME to settings.py (from SITE_NAME env var)
- Replace all getattr(settings, "SITE_URL", ...) with settings.NUXT_BASE_URL
- Replace all getattr(settings, "SITE_NAME", "Our Shop") with settings.SITE_NAME
- Fix order_confirmation template using non-existent item.get_total_price
  (model property is total_price — item totals were silently empty in real emails)
- Fix inactive user email task passing scalar fields instead of user dict
  (template expects user.first_name/user.email which rendered empty)
- Fix order_pending_reminder dark mode: add inline text color on yellow card
- Fix order_delivered link from /orders/ID/review to /account/orders/ID
- Fix email_base.html hardcoded "GrooveShop" title to use {{ SITE_NAME }}
- Update tests to override NUXT_BASE_URL instead of non-existent SITE_URL

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`26ff724`](https://github.com/vasilistotskas/grooveshop-django-api/commit/26ff7247fc1868a0928eceeb8a877f3bdfbcf5db))

## v1.93.0 (2026-04-10)

### Documentation

* docs: update Django version references from 5.2 to 6.0

The project upgraded to Django 6.0.x but CLAUDE.md and README.md
still cited 5.2. Migration file headers are auto-generated and left
as-is (historical record).

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`e272bd4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e272bd40671168882a0a11abbc06e7c5aeb26b8b))

### Features

* feat: Bump Versions ([`701ec00`](https://github.com/vasilistotskas/grooveshop-django-api/commit/701ec002b663979e91c29188e923f8bdeca383ec))

## v1.92.4 (2026-04-06)

### Bug fixes

* fix(deps): sync lockfile with v1.92.3 version bump

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`be20ad3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/be20ad31a1a45f2196d6976605aa87277ab3aa81))

* fix(db): set CONN_MAX_AGE to 0 when psycopg pool is enabled

Django's postgresql backend raises ImproperlyConfigured("Pooling
doesn't support persistent connections.") when CONN_MAX_AGE is
non-zero AND the pool is enabled. The check is `!= 0`, so None is
also rejected.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`85cb79e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/85cb79e4d542fb9bda6fa1da3b926827543ef723))

## v1.92.3 (2026-04-06)

### Bug fixes

* fix(deps): sync lockfile with v1.92.2 version bump

The v1.92.2 release commit bumped pyproject.toml but not uv.lock,
causing uv sync --locked to fail in CI.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`3cd79fe`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3cd79fe7e616b46560738aeb38682a9fe2e5a491))

* fix(db): enable psycopg connection pool to bound ASGI DB connections

Django docs explicitly recommend disabling persistent connections
(CONN_MAX_AGE) when running under ASGI and using the database backend's
built-in pool instead. Without this, asgiref's per-request
ThreadPoolExecutor leaks Django thread-local connections that are only
released when the worker thread is GC'd, exhausting Postgres
max_connections under load.

Switches DATABASES.default.OPTIONS to use psycopg_pool (Django 5.1+
native support) with a bounded pool (default min_size=2, max_size=8,
timeout=10s). All knobs are env-var configurable. CONN_MAX_AGE is set
to None when the pool is enabled because the pool manages connection
lifetimes itself.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`8041546`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8041546ce19e73909f61e9bce556c768580c8d76))

## v1.92.2 (2026-04-05)

### Bug fixes

* fix(deps): sync lockfile with v1.92.1 version bump

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`52cd75a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/52cd75a9f0f3c379bd35665bd58ef75d0f268967))

* fix(asgi): move TokenAuthMiddlewareStack import after django.setup()

The import triggered AnonymousUser model loading before
get_asgi_application() called django.setup(), causing
AppRegistryNotReady on daphne startup.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`8fcd225`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8fcd225969a2ac7827422f293b5e44557afeb38d))

## v1.92.1 (2026-04-05)

### Bug fixes

* fix(deps): move pyjwt from dev to production dependencies

allauth's Facebook provider imports jwt at runtime (jwtkit module),
but pyjwt was only in dev dependencies. Production Docker images built
with --no-dev crash with ModuleNotFoundError: No module named 'jwt'.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`fca792a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fca792aa5532c3c51d0413b94f79288574611a03))

## v1.92.0 (2026-04-05)

### Bug fixes

* fix(test): update review test for read-only status field

The status field is now read-only in ProductReviewWriteSerializer
(security fix). Update test to assert the original status is preserved
when a user sends a status value.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`75b9892`](https://github.com/vasilistotskas/grooveshop-django-api/commit/75b9892ecc4a577c71a9fd2ceab50f1170654fe5))

* fix(ci): set DEBUG=True in CI environment variables

When DEBUG defaults to False (our security fix), SECURE_SSL_REDIRECT
becomes True and STATIC_URL changes, causing 301 redirects on every
test request in CI. CI needs DEBUG=True at settings load time —
conftest.py already overrides it to False for actual test execution.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`61a46ff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/61a46ff323cf48b80f834e9771e2c143f43a8570))

* fix(ci): remove --no-migrations and fix coverage source for parallel

--no-migrations breaks CI because pg_trgm extension is installed via
migrations. Also fix coverage source from ["*"] to ["."] and add
thread concurrency for correct parallel coverage collection with xdist.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`b7fc190`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b7fc1906e34fc1f15aa8d542e0a5306ba599e3a6))

* fix(settings): add public whitelist for get_setting_by_key endpoint

The frontend needs access to checkout and loyalty settings without
admin auth. Instead of making the endpoint fully public, introduce
a PUBLIC_SETTING_KEYS whitelist — only whitelisted keys are accessible
without authentication, all other keys require admin access.
list_settings remains admin-only.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`cbefa22`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cbefa22b8df001343ad52a8b6b2b1031742300a8))

* fix(docker): use daphne instead of uvicorn for production CMD

Daphne is the official recommended ASGI server for Django Channels
per both Django and Channels documentation. The previous change
incorrectly used uvicorn which is not the recommended server for
Channels WebSocket support.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`a542e67`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a542e6706da3ace09c22bc8bd7ec7ce9b65d626b))

* fix: resolve critical security, data integrity, and performance issues

Security:
- Default DEBUG to False, crash on missing SECRET_KEY in production
- Add auth to list_settings, get_setting_by_key, notifications_by_ids
- Make review status/is_published, comment user, paid_amount read-only
- Validate search language_code to prevent Meilisearch filter injection
- Fix open redirect in SocialAccountAdapter via URL allowlist

Data integrity:
- Remove duplicate order_canceled signal (double emails/loyalty reversals)
- Guard post_create_historical_record for Product instances only
- Change OrderItem.product on_delete from CASCADE to PROTECT
- Add paid_amount_currency to all update_fields for django-money
- Skip pre_save DB query when update_fields excludes status
- Fix federated search string-to-int ID conversion for result enrichment

Performance & CI:
- Enable parallel coverage in CI (-n auto instead of -n0)
- Add --no-migrations, --dist worksteal, hypothesis CI profile
- Reduce test timeout 600s to 120s, strip test middleware
- Set CONN_MAX_AGE=600, session backend to cached_db
- Fix Dockerfile CMD to uvicorn, add --no-dev, fix compose && bug

Correctness:
- Fix my_orders double-pagination, meilisearch --batch_size arg type
- Reorder Celery config_from_object before conf.update for time limits
- Fix LoyaltyTierTranslation to use TranslationsForeignKey
- Fix NotificationUser related_names and admin references
- Remove uv add lockfile mutation from CI

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`0182b5c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0182b5cfa5c0e006453d0d6ba70d3b26b4a8b95d))

### Features

* feat: Update schema.yml ([`7c44fec`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7c44feccd8868b79cc6ad54d02f47f29a23e6b27))

## v1.91.1 (2026-04-04)

### Bug fixes

* fix(ci): use full semver tag for setup-uv v8 (immutable releases)

setup-uv v8 removed major/minor tags (@v8, @v8.0) for supply chain
security. Must use full semver @v8.0.0 per GitHub immutable releases.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`b5c1476`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b5c1476a6b46a915beae04354521f05fb31b0ede))

* fix(ci): revert setup-uv to v7 (v8 does not exist yet)

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`10990ee`](https://github.com/vasilistotskas/grooveshop-django-api/commit/10990ee1174fe483c4374644119963d362e1a795))

* fix: uv lock ([`a6e5b03`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a6e5b03a63020adc29e7b6416c3eea9525ca55a2))

### Continuous integration

* ci: optimize CI/CD workflows for speed and best practices

- Add concurrency groups to cancel stale runs on new pushes
- Add paths-ignore to skip CI on docs-only changes
- Run quality and testing jobs in parallel (was sequential)
- Bump astral-sh/setup-uv v7 → v8
- Consolidate env vars at job level (remove per-step repetition)
- Add timeout-minutes to all jobs
- Remove broken coverage comment artifact upload
- Remove single-entry matrix strategy
- Add SBOM and provenance attestations to Docker builds

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`b4467e8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b4467e8ec7892e8f80b592522b1bbc6c9f2eb224))

## v1.91.0 (2026-04-04)

### Bug fixes

* fix(tests): update integration tests for new viewset permissions

Align test authentication with permission classes added in bd2cddbb.
Admin-required viewsets now authenticate as staff/superuser, auth-required
viewsets authenticate as regular user. Also fix cart stock reservation
expected status (409 Conflict) and order signal test (invoice generation
disabled).

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`814a609`](https://github.com/vasilistotskas/grooveshop-django-api/commit/814a609cb901caba10b65c4257f0cd2217aae1d1))

### Chores

* chore: Bump Versions ([`50c9520`](https://github.com/vasilistotskas/grooveshop-django-api/commit/50c9520398aded248782c550b33323652680ffee))

### Features

* feat: harden API security and optimize queries

Add explicit ViewSet permissions (IsAdminUser for writes, AllowAny/
IsAuthenticated for reads), HTML sanitization via nh3 on rich-text
fields, HMAC signature verification on Viva Wallet webhooks, and
rightmost-XFF IP extraction in rate limiting middleware.

Optimize order querysets with pre-aggregated totals and prefetched
history to eliminate N+1 queries. Batch-fetch products in order
validation. Consolidate product attribute Meilisearch indexing.

Remove dead redirect_to_frontend code, redundant exception wrappers
in BaseModelViewSet, and echo behavior in WebSocket consumer. Bump
uv to 0.11.3 and update dependency versions.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`bd2cddb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bd2cddbb3a095e00b06ff47774ae45f921b0843f))

## v1.90.0 (2026-03-26)

### Features

* feat: Bump Versions, type fixes ([`f016c78`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f016c78f6ec9de07c7cc437030edc33692ead7a7))

## v1.89.3 (2026-03-23)

### Bug fixes

* fix(checkout): include shipping and fees in payment amount

Cart checkout now calculates shipping cost and payment method fee
before initiating Stripe/Viva checkout, ensuring the payment amount
matches the final order total. Stripe receives shipping as a separate
line item for proper breakdown on the checkout page.

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`577e078`](https://github.com/vasilistotskas/grooveshop-django-api/commit/577e078761ed9944bf0b3c8279e9b37e2f1610ae))

## v1.89.2 (2026-03-23)

### Bug fixes

* fix: viva webhook ([`673a706`](https://github.com/vasilistotskas/grooveshop-django-api/commit/673a70667969a4e81ac875dac26a2b7159f777c8))

## v1.89.1 (2026-03-23)

### Bug fixes

* fix: redis cache and exclude_session_id from get_available_stock ([`7a7d58c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7a7d58c52bc58f6454d4d59441da5a16f45c950b))

## v1.89.0 (2026-03-22)

### Features

* feat: Bump Versions ([`a26c951`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a26c95187a0e9c1ca6bc47152d342bfcc39f613a))

## v1.88.3 (2026-03-18)

### Bug fixes

* fix: Remove viva-payment-api.yaml ([`6d92314`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6d923144bfb8ee9df8019858b24dd535d13f2310))

### Refactoring

* refactor: optimize task execution and database queries

- Replace blocking async_result.get() calls with Celery chains
  to improve worker concurrency
- Add select_related/prefetch_related to order task queries to
  reduce N+1 queries
- Move cache lock cleanup to finally block for reliable cleanup
- Add db_index=True to is_deleted field for soft delete queries
- Replace f-string logging with parameterized logging in search
  middleware
- Remove unnecessary refresh_from_db() calls from metadata methods

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> ([`691041d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/691041defc57ec4c36215321bcabf84e43c0ac04))

## v1.88.2 (2026-03-17)

### Bug fixes

* fix: uv lock ([`9690b22`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9690b225eae47dce0e4ce5b0e47b5875206e375f))

* fix: ci ([`9383c0b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9383c0b2c4da2f1ac8be7bcc946691ca55158451))

## v1.88.1 (2026-03-17)

### Bug fixes

* fix: ci ([`8b9096b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8b9096b7f30007403235ee7ca1d20f41e935a5a7))

## v1.88.0 (2026-03-17)

### Features

* feat: Bump Versions ([`3b7c520`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3b7c520679dd974e09da30864b283115f8aa2488))

## v1.87.0 (2026-03-06)

### Features

* feat: Bump Versions, type fixes ([`74d339d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/74d339d51bf3121d2f844aca9dede0a3666c0fcc))

* feat(order): add viva wallet payment support

Extend order-first flow to handle redirect-based online providers
(Viva Wallet) alongside offline payments. Add resolve-order endpoint
for frontend redirect after payment. Refactor webhook handler to
batch field updates in a single save. Bump python-dotenv and ty.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> ([`50d797d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/50d797d98600dcf3f92cc8b20ae28cb317886591))

## v1.86.2 (2026-03-02)

### Bug fixes

* fix: uv lock ([`5396e55`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5396e5552eb125503c84885eb3fa808668aac4b6))

* fix: dashboard ([`55bde03`](https://github.com/vasilistotskas/grooveshop-django-api/commit/55bde030758f5db56f7fc89a0734d7f0d97e366d))

## v1.86.1 (2026-03-01)

### Bug fixes

* fix: update uv.lock ([`c84558c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c84558ce23b26afae3182f87ea48d4dc5aed38dd))

* fix: redits

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> ([`03d708f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/03d708fa436d8700c8ee13d8094c1d2533c54620))

## v1.86.0 (2026-03-01)

### Build system

* build: upgrade django to 6.0 and update deps

- Upgrade Django 5.2.9 → 6.0.2, boto3, django-celery-beat,
  django-unfold, pillow, urllib3, disposable-email-domains
- Add Redis password support in settings (REDIS_PASSWORD env var)
- Use REDIS_URL for channel layers config
- Improve StockManager TTL validation with type/range check
- Switch test cache to LocMemCache for xdist isolation
- Minor formatting in order views and Viva Wallet settings

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> ([`bf5252a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bf5252ae07ffaf762e680a59e5340f9846e0dabd))

### Features

* feat(payment): Viva Wallet ([`cd8089d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cd8089de3112bfe978db4c75e5df32ddb7cbd676))

## v1.85.2 (2026-02-28)

### Bug fixes

* fix: test ([`3bddfb8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3bddfb85b36018667b467c6b6555ef13829e6a07))

* fix(security): remove all sensitive data sources from webhook logging

Remove reads of HTTP_STRIPE_SIGNATURE and DJSTRIPE_WEBHOOK_SECRET
entirely from the logging method so CodeQL taint analysis cannot
trace any sensitive data flow to the logger. Also remove response
body preview from failure logs.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> ([`b718530`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b718530cac0b79c113542d1eaf3c7d6481b16a72))

* fix(security): resolve 22 CodeQL code scanning alerts

- Replace xml.etree.ElementTree with defusedxml to prevent XML bomb
  and path injection attacks in SVG validation (HIGH)
- Remove sensitive data (signature values, secret prefix, IPs, body
  preview) from Stripe webhook debug logging (HIGH)
- Replace str(e) in HTTP error responses with generic messages across
  order views, email admin views, core settings views, and subscription
  views to prevent stack trace information exposure (MEDIUM, 19 alerts)
- Add proper logger.error() calls where missing before returning
  generic error responses

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> ([`b9aa54e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b9aa54e99efbfca32f799042d001b83340f2e18b))

## v1.85.1 (2026-02-28)

### Bug fixes

* fix: update lock ([`87b4963`](https://github.com/vasilistotskas/grooveshop-django-api/commit/87b4963d166842ec1c4db16b5cf597aaad98c487))

### Refactoring

* refactor(rosetta): move translation version bump to signal handler

The translation version should be bumped when Rosetta actually saves
.po files to disk, not in CacheClearingRosettaStorage.set(). The
storage backend's set() method is only called for internal cache
operations, not during the actual translation save. This moves the
logic to the post_save signal handler where it belongs.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> ([`139e8ec`](https://github.com/vasilistotskas/grooveshop-django-api/commit/139e8ec2ba0573ac697e16bd241c3c65c6dc4dd6))

## v1.85.0 (2026-02-28)

### Bug fixes

* fix(tests): test_stock_manager.py ([`765d4e2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/765d4e2b01611b78c050c6ee27c50bb23cb7b999))

* fix: test_stock_manager.py remove duplicate @pytest.mark.django_db(transaction=True) ([`83f88d8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/83f88d881aedb44a678e857622b87820e7812a18))

### Features

* feat(rosetta): sync translation files via Redis across K8s pods

Add a Rosetta post_save signal handler that stores .po/.mo file
contents in Redis after each translation save. The translation
reload middleware now writes these files from Redis to disk before
reloading gettext catalogs, solving NFS attribute cache staleness
in multi-replica deployments.

Also skip version bump for Rosetta health-check keys and fix
duplicate transaction marker in stock manager test.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> ([`bcb1026`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bcb1026a6fdc4cbfe3973a58124900d34d48c2b0))

## v1.84.0 (2026-02-27)

### Features

* feat(auth): add rate limiting and tighten Knox token security

- Add AllAuthRateLimitMiddleware for /_allauth/ headless endpoints
  using Redis sliding-window counters (login: 10/min, signup: 5/min)
- Reduce Knox TTL from 20d to 7d; enable AUTO_REFRESH (1-day min)
- Revoke all Knox tokens on password change via password_changed signal
- WebSocket auth now only accepts ?access_token= (drop session_token)
- Delegate social email auto-connect to allauth settings instead of
  custom pre_social_login implementation
- Fix ALLOWED_HOSTS wildcard only in DEBUG; SSL redirect on in prod

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com> ([`51c6f9a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/51c6f9acef3e8c1fd2702e5fd847126fe2dd91f7))

### Refactoring

* refactor: add ty type checker and modernize codebase

- Add ty 0.0.19 as dev dependency with project-wide config,
  suppressing rules for Django ORM, DRF, and test patterns
- Fix type annotations across core, admin, and domain modules
  for ty compatibility (null guards, Promise types, etc.)
- Modernize admin display methods to use @admin.display()
  decorator pattern throughout all admin modules
- Bump ruff 0.15.2→0.15.4, phonenumbers 9.0.24→9.0.25

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> ([`da56ffc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/da56ffc921d2cb09e6955574fb94ab99b5dbc4b2))

## v1.83.0 (2026-02-25)

### Bug fixes

* fix(ci): Bump Versions ([`353ab1a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/353ab1af636c64950ee4c5f2898fc7a42ced8c21))

### Features

* feat: Claude ([`8165cf9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8165cf9a0864a2aae0353a68430a817f6c8fc03c))

## v1.82.4 (2026-02-25)

### Bug fixes

* fix: product signal ([`faaa841`](https://github.com/vasilistotskas/grooveshop-django-api/commit/faaa841f6cb1549138aba516f894f0eae45074a8))

## v1.82.3 (2026-02-24)

### Bug fixes

* fix: harden concurrency and access controls

Add select_for_update() locking for stock reservations, order
cancellation, loyalty redemption, and sortable model deletion.
Use F() expression for atomic blog view count increment. Wrap
cart merge in transaction.atomic. Prevent duplicate loyalty point
expirations with Exists subquery.

Restrict soft-delete filters, private metadata filter, and search
analytics endpoint to staff/admin users. Move SecurityMiddleware
to top of middleware stack. Close Redis connection in health check.

Fix SortableModel.save() skipping super().save() when sort_order
was already set. Fix Celery lock release in clear_duplicate_history
to not release on retriable errors. Switch scheduled backup from
blocking .apply() to .apply_async() with timeout. Enable Celery UTC.

Move social avatar download to Celery task. Convert async product
price signal to sync. Add retry config to notification task.
Prefetch main product image to fix N+1 in for_list().

Update deps: psycopg 3.3.3, uvicorn 0.41.0, ruff 0.15.2, and
others. Refresh README and CLAUDE.md documentation.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> ([`4a6547a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4a6547ac812f594e1b92107258afbc138b2ae837))

### Chores

* chore: CLAUDE.md ([`f5e9a90`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f5e9a90432ec76e955ec69e10fc19045d06b6391))

### Testing

* test: remove all 14 xfail markers and fix flaky tests

- Replace set_current_language()+save() with update_or_create() in
  SubscriptionTopicFactory to prevent parler translation PK collisions
- Convert 3 unimplemented provider xfails (PayPal/FedEx/UPS) to skip
- Remove unnecessary transaction=True from test_exception_types and
  test_transaction_rollback (eliminates xdist flush conflicts)
- Add threading.Barrier to 9 concurrent stock tests for reliable
  thread synchronization, add missing connection.close() calls

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com> ([`f44c88f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f44c88f7e2881c5cedcd7f183edd7151b522d42b))

## v1.82.2 (2026-02-14)

### Bug fixes

* fix: cache ([`816b4da`](https://github.com/vasilistotskas/grooveshop-django-api/commit/816b4da55051f9f4a3d925f1792617d956a59863))

## v1.82.1 (2026-02-12)

### Bug fixes

* fix: test ([`32befe4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/32befe4eac7789a81f7a1e9d4d6d72a747b9967c))

* fix: upload_image ([`905fd5d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/905fd5dbe4b2202a3ee228c49c6bc2b91eb56d50))

## v1.82.0 (2026-02-11)

### Features

* feat: loyalty progress ([`21ef72f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/21ef72fd24ded8c217d612c4525f0911a6996d17))

## v1.81.0 (2026-02-11)

### Features

* feat: add loyalty points command ([`27110ec`](https://github.com/vasilistotskas/grooveshop-django-api/commit/27110ecdcff2a8b8b8a3e6af84f90615238d2bdf))

## v1.80.0 (2026-02-10)

### Features

* feat: loyalty progress ([`c4f5304`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c4f5304270082e9bcfbcb5511051a4b18e1a1679))

## v1.79.1 (2026-02-10)

### Bug fixes

* fix: uv ([`9bb8df0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9bb8df07b1ca48841e8429b5b3c2576c194f7fb0))

* fix: update_view_count ([`5ef3dcc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5ef3dcc3ca72c7e5f2de819f95b3548801f2f198))

## v1.79.0 (2026-02-10)

### Features

* feat: loyalty tiers ([`c2c0419`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c2c0419c3a01cabb44117e709c9f91c08acc8f7a))

## v1.78.0 (2026-02-08)

### Features

* feat: loyalty system ([`cc32b58`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cc32b588ad0a16664696b95593045f806a487ad7))

## v1.77.0 (2026-02-07)

### Features

* feat: rewrite open api schema generation methods ([`c012062`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c0120628f559db5356e97eaba8ac152f8c61aea9))

## v1.76.1 (2026-02-07)

### Bug fixes

* fix: settings permissions ([`2b895d9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2b895d9df7c1daf1e42a4c96412e177a5ce07086))

## v1.76.0 (2026-02-07)

### Bug fixes

* fix: cache ([`1c2bfc2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1c2bfc2e423ab483a133794e5ca8afe7c1e5ceae))

### Features

* feat: Remove useless codebase ([`1fb077b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1fb077bf2d3c314968c33c6b4e98583d176ea80d))

## v1.75.0 (2026-02-06)

### Bug fixes

* fix: Remove useless files ([`4d97432`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4d9743252552d8df26fbbe80ce45662d44237fb5))

### Features

* feat: improvements ([`798b9a8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/798b9a83d60e5aa4613f9aaf5f37b7df95eda48d))

## v1.74.0 (2026-02-05)

### Features

* feat: code cleanup ([`ca4f513`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ca4f513c565e5290d6b030f3aac25b45db1c3f09))

## v1.73.2 (2026-02-04)

### Bug fixes

* fix: lint ([`a908e56`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a908e5698578a9b0275d9bfc7b9b7a45b55f304c))

## v1.73.1 (2026-02-04)

### Bug fixes

* fix: attributes ([`619946a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/619946abbcfe30f25b59deca03844b5d1552d210))

## v1.73.0 (2026-02-03)

### Bug fixes

* fix: product admin performance ([`8786d31`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8786d31a3a4283c5e1e689ce60223622bab2a59c))

* fix: test ([`eb93e86`](https://github.com/vasilistotskas/grooveshop-django-api/commit/eb93e86be26a174352c826fc6c8392b3421a6d65))

### Features

* feat: Product Attributes ([`98551cf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/98551cf97018c710f875f5c72bf99363e70e2d4c))

* feat(product): Add product attributes to OpenAPI schema

- Add Attribute and AttributeValue endpoints (GET, POST, PUT, PATCH, DELETE)
- Include translation support for all three languages (el, en, de)
- Add ProductAttribute nested serialization in Product schema
- All fields use camelCase naming convention
- Validates requirement 6.8 ([`f3a7967`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f3a7967671faf1c925621f7bde7c1733bd84717a))

* feat: improve admin settings UI/UX ([`a3d2e91`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a3d2e917466ef1da7de32d08855ed1a9f1b1803f))

* feat: manager improvements ([`36c95a9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/36c95a90bb3a6a53d100988ccc8a21c6e7fa7b25))

## v1.72.3 (2026-02-02)

### Bug fixes

* fix: queries ([`43c4956`](https://github.com/vasilistotskas/grooveshop-django-api/commit/43c4956a730f553419ca4cce3b944ef39edc5cd8))

* fix: search ([`f029efd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f029efdf4303b863441b23705a153d2dd9462f60))

## v1.72.2 (2026-02-02)

### Bug fixes

* fix: tests ([`a3a7c51`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a3a7c512a8784765f75b8b6e92bb553bf10cf86e))

* fix: active products, TranslatedFieldExtended ([`49e12b3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/49e12b3b2a394432ce78b60ddec7d3b3ad97dd27))

## v1.72.1 (2026-02-01)

### Bug fixes

* fix: lint ([`56db3a9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/56db3a94cefa228f3f940b0ad526ad3669ca3725))

* fix: search ([`690df05`](https://github.com/vasilistotskas/grooveshop-django-api/commit/690df05dee6de6a705d1830aa89e2641e6e60fb7))

## v1.72.0 (2026-02-01)

### Features

* feat: search improvements ([`0d236b9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0d236b97eb7221b3dcf574d1db123999881624ad))

## v1.71.0 (2026-02-01)

### Bug fixes

* fix: lint ([`8659437`](https://github.com/vasilistotskas/grooveshop-django-api/commit/865943729fbe904871b1883030c7f522ff1dddf1))

* fix: tests ([`dd32c03`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dd32c03129cbd343c33a592665a6b67ba4a9d793))

* fix: tests ([`d833ac5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d833ac5aeb770138fbcf4a96cc4d27f671f229b3))

* fix: remove price validation ([`f533a49`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f533a49bb5843a91c2ec873b18567ca0387b3ef7))

### Features

* feat: Search improvements ([`1c8aa9c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1c8aa9cda5d00c382c621bdaf4600dcd9a37c7aa))

* feat: search improvements ([`1372e09`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1372e0994f53be84a33b012224de9444d77e1bdd))

* feat: Product search improvements ([`f51a13f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f51a13f580e9a3f0228ccb760b8775007c6f04f5))

## v1.70.1 (2026-01-28)

### Bug fixes

* fix: dashboard ([`eec580a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/eec580a1fa6d6c060eb2dec2b45784f3c644e2e5))

## v1.70.0 (2026-01-28)

### Bug fixes

* fix: redunant test remove ([`517c119`](https://github.com/vasilistotskas/grooveshop-django-api/commit/517c11948fa879e58afe6b4336e4677caad17459))

* fix: dashboard UI ([`0cd0c4f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0cd0c4f11571a850149ce5b33bb6e86d20b78063))

* fix: dashboard UI ([`7e009dc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7e009dc17cc309e5dbd37b1100fce986043e5c3f))

* fix: rosetta button ([`1fdeaed`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1fdeaed3279d79f5dfe59e02f320f342e6d26c24))

### Features

* feat: dashboard enhancements ([`b5f8b0f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b5f8b0f5466c2cf023a221a065e1a5718c62f6b9))

* feat: dashboard colors ([`6c172e2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6c172e210edd3096d0935a6ef85258d746895644))

## v1.69.0 (2026-01-28)

### Bug fixes

* fix: ruff ([`dce35a4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dce35a4b05b6e652ccfe0dbc8c6c2c2e9d0e2c55))

### Features

* feat: dashboard enhance ([`5ef3bf4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5ef3bf41a3d2c8807cb5627762f1e6b93e592f4b))

* feat: Bump Versions, Improve testing ([`10c35a5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/10c35a58d28c82156bbd6ec73d7e16f9c8e4ad5d))

* feat: order STOCK_RESERVATION_TTL_MINUTES from extra settings ([`29123a5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/29123a5d9710d2f2a1385b76188d39f1d00956bc))

## v1.68.0 (2026-01-26)

### Features

* feat: stock manager and order improvements ([`7311fc9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7311fc9b35909e5a69246c371ec1a91b2dcd31da))

## v1.67.1 (2026-01-21)

### Bug fixes

* fix: order creation ([`45afb26`](https://github.com/vasilistotskas/grooveshop-django-api/commit/45afb26116c6388e255991ed36cdcdb43355ff02))

## v1.67.0 (2026-01-21)

### Features

* feat: bump versions, clean cart at order creation ([`dba07fc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dba07fc989e3e1cee42bbb209961ea62b0355398))

## v1.66.1 (2026-01-19)

### Bug fixes

* fix: email templates ([`1d5c365`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1d5c3659ad3d2a03eea0975c3c078c6011964c43))

## v1.66.0 (2026-01-19)

### Bug fixes

* fix: lint ([`a5c2e5f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a5c2e5f1039fe7ee4b04d8be38503bfcf782a55d))

* fix: rosseta ([`5db1dab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5db1dab71ec57071c7b355546684be4d2f7f754f))

### Features

* feat: stripe webhook debug ([`e37a7ba`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e37a7ba57aa38e6fa49e44c1c778c289dbf5cc4f))

## v1.65.1 (2026-01-17)

### Bug fixes

* fix: email preview locales ([`4cac197`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4cac197fdf9933e2afb4dc2b16e4d547c115ea92))

## v1.65.0 (2026-01-17)

### Bug fixes

* fix: ruff ([`75d0676`](https://github.com/vasilistotskas/grooveshop-django-api/commit/75d0676c24877f1d30cf6d12b14be76bfd996ea0))

### Features

* feat: pay way image, bump versions ([`64d3c1c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/64d3c1c2fcb62b197a5d200bf1d371db1fc7581d))

## v1.64.0 (2026-01-08)

### Bug fixes

* fix: cart service ([`c6ecb84`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c6ecb84ae24c713760f4eeb7d9829325245b33ab))

### Features

* feat: perf improve ([`dbcfb45`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dbcfb4537add16eebd53872f6e0289113786766c))

## v1.63.0 (2026-01-08)

### Bug fixes

* fix: tests ([`b59abb7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b59abb78c21ac2900f9b9d64f26df5c43d630a6f))

### Features

* feat: Bump Versions ([`e3d838f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e3d838f93ed87de82c0caf7cb0c7f1516cbbd71e))

## v1.62.0 (2025-12-18)

### Features

* feat: Bump Versions ([`adb1b0d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/adb1b0d68e94ad3245846970d7120976c103e908))

## v1.61.0 (2025-12-12)

### Features

* feat: Bump Versions ([`192fab0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/192fab0dcba0b0f145325048f9a67e0f4e45ed90))

## v1.60.0 (2025-12-11)

### Features

* feat: Bump Versions ([`485c62e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/485c62ea2dd649335a4d0a81891c6f37c2edd8e1))

## v1.59.1 (2025-12-07)

### Bug fixes

* fix: performance ([`6067022`](https://github.com/vasilistotskas/grooveshop-django-api/commit/60670226246b4ab65fbb5226bab109c181cfae84))

## v1.59.0 (2025-12-06)

### Bug fixes

* fix: lint ([`dd3dd06`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dd3dd06aacd2ee4826dd313c9785dfc4fe9a9c55))

### Features

* feat: optimize ([`0c7ab0e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0c7ab0e11b599279995309ebb45934802e1b4795))

## v1.58.0 (2025-12-05)

### Features

* feat: Bump Versions ([`0d13b0e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0d13b0e096933ea6f740a123fbb438890a8052c7))

## v1.57.0 (2025-12-03)

### Features

* feat: Bump Versions, remove useless files ([`7c9b1a4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7c9b1a4db1015880c2340a8654b4ddbf2a1a4a66))

## v1.56.2 (2025-11-25)

### Bug fixes

* fix: tailwind ([`62088c3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/62088c3d1a2d19c6f05e0009d9b6b5e8b9a12177))

## v1.56.1 (2025-11-25)

### Bug fixes

* fix: tailwind ([`70f1897`](https://github.com/vasilistotskas/grooveshop-django-api/commit/70f1897eb8390a9bc413ea1e8a524365e2d5b043))

## v1.56.0 (2025-11-25)

### Features

* feat: tailwind 4 ([`ad3a8ff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ad3a8ffed361e5811d002514239e5aa4cb914b99))

## v1.55.1 (2025-11-25)

### Bug fixes

* fix: email templates ([`448f308`](https://github.com/vasilistotskas/grooveshop-django-api/commit/448f308ac3e85134ce069c75a21140d808520c76))

### Chores

* chore: remove useless files ([`52d96f8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/52d96f88a3508c9b85d880b7739d81d56c12b291))

## v1.55.0 (2025-11-24)

### Bug fixes

* fix: meili task ([`c701fb6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c701fb63df45f0e26a5b84213c36958e87a1003c))

### Features

* feat: subscription confirmation, task fixes ([`a79529c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a79529ca7f75fdf1eb1447d9dce05da57b6346da))

## v1.54.0 (2025-11-24)

### Bug fixes

* fix: tasks ([`6088b14`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6088b14b90d3df2892090af559a9d606d437831c))

### Features

* feat: celery improvements ([`1b7441b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1b7441b0138bd69c001c565cbc1aa1ef20796bc2))

## v1.53.1 (2025-11-24)

### Bug fixes

* fix: remove useless stuff ([`5bd58a5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5bd58a55aa60b54a4409fff1635f63ba5de6a7ac))

## v1.53.0 (2025-11-23)

### Bug fixes

* fix: ruff ([`ea4766c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ea4766cf76cf1fc65d220966fd047a98d91e9729))

### Features

* feat: sync meili task ([`bc2257c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bc2257c05557f5fdde55c27d96cb67b334d0d090))

## v1.52.0 (2025-11-23)

### Features

* feat: admin emails ([`a57b9b9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a57b9b922525ce700e9bd275f0310de03e449d7b))

## v1.51.0 (2025-11-23)

### Features

* feat: Bump Versions ([`43b1925`](https://github.com/vasilistotskas/grooveshop-django-api/commit/43b1925f51cc871c768a56c915d235c55558fcca))

## v1.50.0 (2025-11-20)

### Features

* feat: Bump Versions ([`f050305`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f0503057e5293806f883251acf6d006dea98a6cd))

## v1.49.0 (2025-11-11)

### Features

* feat: Bump Versions ([`bd7ef22`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bd7ef22816daff22c22cd4df595ca72eb96bace6))

## v1.48.0 (2025-10-30)

### Bug fixes

* fix: tests ([`a071fbb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a071fbb7b013c5c442d62115084b367a3f95a8bd))

### Features

* feat: Bump Versions, pg pool enable ([`3929ef3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3929ef31a18e667989398a235f9bfd0c78a32c5b))

## v1.47.0 (2025-10-28)

### Bug fixes

* fix: tests ([`0a9df8a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0a9df8a584411ffee8ff09ac77d7d06cea1f56cd))

### Chores

* chore: Bump Versions ([`f894188`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f8941882b3626f07a41ed9499bc8f17e86e35c71))

### Features

* feat: Improve PayWayTranslationFactory and update dependencies

Refactored PayWayTranslationFactory to use more realistic Faker-generated data for description and instructions, and updated the name field to use PayWayEnum values. Updated pyproject.toml dependencies and Python version requirement, including boto3, django-tinymce, meilisearch, phonenumbers, psycopg, python-dotenv, disposable-email-domains, djangorestframework-stubs, Faker, ruff, rpds-py, and psutil. ([`f96c0ff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f96c0ffae318544ba6def4f7bd14e88ee03c0266))

* feat: Enhance factory data realism and update UV version

Updated the UV version to 0.9.5 in CI and Dockerfiles. Improved all factory classes to use more realistic and varied fake data for translations, comments, notifications, products, categories, tags, addresses, and VAT rates, enhancing test coverage and data diversity. Adjusted probability and value ranges for boolean and numeric fields to better reflect real-world scenarios. ([`c6c2372`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c6c2372c4ca2066d05ebb373d59bf8ef40dd0538))

## v1.46.0 (2025-10-19)

### Features

* feat: Add settings API endpoints and dynamic shipping logic

Introduces API endpoints to list and retrieve settings via /api/v1/settings and /api/v1/settings/get. Shipping price and free shipping threshold are now configurable via settings and used dynamically in order creation and Stripe payment integration. Also updates serializers and schema to reflect these changes, and makes shipping_price read-only in order creation. ([`cf49628`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cf49628c01e9ff4823d0c034e54ed9425b8f2bf5))

## v1.45.0 (2025-10-17)

### Features

* feat: Bump Versions redis ci version to 8 ([`5819d6c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5819d6c9015d22ef40b604ddfa7f1aaa59b43a7a))

## v1.44.1 (2025-10-16)

### Bug fixes

* fix: remove `seo` app and move SeoModel in core, admin remove `format_html` and use `mark_safe` and bump versions ([`ccbfe54`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ccbfe54a792ed73e43ad290b7a7741e297423e2b))

## v1.44.0 (2025-10-15)

### Features

* feat: Bump Versions ([`f21aa96`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f21aa964aead23d0569414eb2bd4999315a72cb8))

## v1.43.1 (2025-10-13)

### Bug fixes

* fix: sync ([`bc4e6ff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bc4e6ffa40f8c914c701eecbc1bd3c251e81c734))

* fix: Dockerfile ([`a810f83`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a810f831984e51fc5513697d83c991a6bbaa2f19))

## v1.43.0 (2025-10-13)

### Features

* feat: remove session_key from cart ([`0382682`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0382682648b8a2cc77cfba07f384d638efcfd2e3))

## v1.42.0 (2025-10-12)

### Features

* feat: Upgrade to PostgreSQL 17

Update all Docker and CI configurations to use PostgreSQL 17 instead of 16. ([`8a8acf7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8a8acf7cba116565a43b67ca84f2cf814f50baec))

## v1.41.0 (2025-10-12)

### Features

* feat: Update Meilisearch version and expand Greeklish tests

Bump Meilisearch version from 1.17.1 to 1.22.3 in CI and Docker Compose. Add more Greeklish-to-Greek test cases for 'Φορτιστής', 'κινητό', and 'Οθόνη' in both integration and unit tests. Remove obsolete pagination documentation. ([`6f865ca`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6f865caa269b20a499bc04c56ac94db39ceddf8d))

## v1.40.0 (2025-10-12)

### Features

* feat: Add guest order support and permissions for orders

Introduces guest (unauthenticated) order creation and management, including a new IsOwnerOrAdminOrGuest permission class. Updates order view logic to allow guests to create, retrieve, and cancel their own orders via UUID, while restricting access to authenticated users and admins as appropriate. Enhances serializers and schema documentation for guest orders, and adds comprehensive integration tests for guest order scenarios. ([`0fdb3d6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0fdb3d66f926a1c14879482488f2b25df12c6430))

## v1.39.0 (2025-10-10)

### Bug fixes

* fix: lint ([`0540d97`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0540d97553436567cf84394d80367977ec4c3b2e))

### Features

* feat: Improve Meilisearch commands and add Greeklish utils

Moved and renamed Meilisearch management commands for clarity and modularity, consolidating sync, clear, drop, and inspect operations. Added a new Greeklish-to-Greek converter utility for search, with supporting tests. Improved Meilisearch queryset result ordering and enabled ranking score/match position in search results. Updated schema.yml to use 'languageCode' instead of 'language' for query parameters. ([`527f7b1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/527f7b1325edb2bfea01759f645893a6da811aa8))

## v1.38.0 (2025-10-09)

### Features

* feat: Upgrade to Python 3.14 and update dependencies

Bump Python version to 3.14.0 across Dockerfiles, config, and documentation. Update development dependencies: add pydantic 2.12.0, rpds-py 0.27.1, and msgpack 1.1.2. Improve image and SVG validation logic, refactor Celery task database connection handling, and simplify test mocks. Remove custom database pool options and set ATOMIC_REQUESTS to False in settings. ([`b8c4c76`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b8c4c764ca084c2ea0dd46393046977857322117))

## v1.37.0 (2025-10-08)

### Features

* feat: Add Meilisearch management commands and update dependencies

Introduces Django management commands for inspecting, reindexing, and updating Meilisearch indices. Also updates several dependencies in pyproject.toml, including boto3, django-phonenumber-field, django-stubs, django-stubs-ext, Faker, and ruff. ([`69f8469`](https://github.com/vasilistotskas/grooveshop-django-api/commit/69f846977624b020e3a540eaa82160cb776a9348))

## v1.36.0 (2025-10-08)

### Bug fixes

* fix: order test missing payment id ([`4a25a59`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4a25a59c81854ff8b98301c0326fcc1578e5410e))

### Features

* feat: Add multilanguage search support and remove CSP package

Introduces multilanguage search capabilities for products and blog posts, including new utility functions and API parameter changes (language -> language_code). Adds and updates synonym lists for English, Greek, and German in search models. Removes Content Security Policy (CSP) middleware, settings, and related views/tests. Updates dependencies and MeiliSearch queryset to support locale filtering. Adds comprehensive integration and unit tests for multilanguage search. ([`1e2a140`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1e2a14091d83acab4932287a8e92519eb8ee79bb))

## v1.35.0 (2025-10-02)

### Features

* feat: Add auto-approve option for blog comments

Introduces BLOG_COMMENT_AUTO_APPROVE environment variable and setting to allow automatic approval of new blog comments. Updates the comment serializer to set approved status based on this setting. Also updates several dependencies: boto3, botocore, django, and django-phonenumber-field, and increases CONN_MAX_AGE to 600. ([`ccbe74a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ccbe74abe562d471228eaf55a3747fea5930ce0a))

## v1.34.1 (2025-10-02)

### Bug fixes

* fix: Reduce REST API default page size to 12

Changed the REST_FRAMEWORK PAGE_SIZE setting from 52 to 12 to provide smaller paginated responses by default. ([`9dac1e8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9dac1e868e4d00cf6c5f3c6277999e8c3d2fbc8d))

### Chores

* chore: order API schemas and update Celery settings

Replaced several order-related request schemas in the OpenAPI spec with more specific types, removed payment/refund endpoints and related schemas, and added new request objects for tracking, canceling, and updating order status. Updated Celery configuration in settings.py for improved reliability, task handling, and resource management. ([`5fe74f4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5fe74f41e2458b4818ebc04a4214224dec205382))

## v1.34.0 (2025-10-01)

### Bug fixes

* fix: Improve order payment and refund test assertions

Added assertions to verify order payment method, payment ID, and paid status in the service order tests. Updated refund error message assertion to check for unpaid order message. Also ensured paid_amount is set and saved with order updates. ([`c6fa60c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c6fa60c78b58d8a620177085cb90de25fef017e1))

* fix: Update order service tests and patch signal import

Patched the signal import path in the cancel order test to use 'order.signals.handlers.order_canceled.send'. Updated assertion in refund_order_not_paid test to check for missing payment ID message. Removed redundant docstrings from several test methods for clarity. ([`9a39a34`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9a39a34c99e428b5028315cf8fd41b7a87d673e3))

### Features

* feat: Refactor order payment and refund handling

Removed payment-related views and serializers from the order app, consolidating payment and refund logic into the OrderService and OrderViewSet. Added new serializers for payment intent, checkout session, refund, and payment status to order/serializers/order.py. Updated signal handlers and logging for clarity and consistency. Enhanced cancellation and refund workflows, including metadata updates and Stripe webhook handling. Cleaned up admin and test files to support these changes. ([`5ebb9b8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5ebb9b8beef7415e233144e1d4b411bbcd772fa0))

## v1.33.0 (2025-09-30)

### Features

* feat: Update Stripe config and order item serializer usage

Refactored StripePaymentProvider to use DJSTRIPE_WEBHOOK_SECRET and store API key in an instance variable. Updated OrderSerializer to use OrderItemDetailSerializer for items, and changed schema.yml to reference OrderItemDetail. Improved password generation to ensure inclusion of digits and special characters when requested. Adjusted related unit test to match new Stripe settings. ([`966c06a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/966c06a2c53d201d2eeeb25bfe5fbe4fbfeda58b))

* feat: Add Stripe Checkout Session support for orders

Introduces Stripe Checkout Session creation for orders, including new API endpoint, serializers, payment provider logic, and signal handlers for session events. Adds metadata fields to the Order model and updates OpenAPI schema documentation. ([`cc6ec0c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cc6ec0c2508ee2d6f88eb7bbce74747c4b895bde))

* feat: Integrate Stripe payments with dj-stripe and add payment intent API

Added dj-stripe integration for Stripe payments, including new environment variables and settings. Implemented Stripe payment processing, refund, and status retrieval using dj-stripe models. Added management command for PayWay setup, new API endpoint to create Stripe payment intents, and corresponding serializers. Updated order payment logic, signals for Stripe webhook events, and OpenAPI schema. Improved tests for Stripe payment and refund flows. ([`5e63ebe`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5e63ebe58962a09978dba0441be98dca6c9b9eb8))

## v1.32.0 (2025-09-28)

### Bug fixes

* fix: Update test to check for 'count' in response data

Changed assertion in NotificationUserViewSetTestCase to verify 'count' key in response data instead of 'info', reflecting updated API response structure. ([`31b0bc2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/31b0bc2c79d28c282b719b83d2cf2e6c54dec24b))

* fix: tests for notification, product, and backup tasks

Changed expected status code in notification user view test to 200 OK. Removed ordering tests by review average and likes count from product view tests. Updated backup database task tests to check 'result_message' instead of 'message'. ([`a8b5769`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a8b57698ad50a804b2dbd5b4a36d22ebd7d5f3c2))

### Features

* feat: notification and order APIs, update serializers

Standardized response fields and error handling in notification views and serializers, removed unused NotificationInfoResponseSerializer, and updated notification URLs to remove trailing slashes. Added 'seen' query parameter to notifications_by_ids endpoint. In order and address serializers, allowed blank values for mobile_phone fields and improved tracking details schema. Order item creation now uses product.final_price. Updated OpenAPI schema to reflect these changes. ([`3df05d8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3df05d895dd5139d9cba343333c8739b139b0ea2))

* feat: blog and core views, filters, and serializers

Replaces MultiSerializerMixin with request/response serializer configs in all viewsets, updates blog filters and managers to use approved/active counts for comments and tags, and ensures non-staff users only see approved comments. Refactors serializer logic for DRF actions, improves annotation usage, and updates related tests and OpenAPI schema. This standardizes serializer handling and improves filtering accuracy for likes, comments, and tags throughout the blog and core apps. ([`13b63e2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/13b63e2a7799cc225fbd1d21abddc3e68c4a5713))

## v1.31.0 (2025-09-26)

### Bug fixes

* fix: missing import ([`8ab65d5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8ab65d5815e09c5e24f067bad60a4b14ed1022ed))

### Features

* feat: extend_schema_field at serializer urls, authentication app removed and added some translations ([`76e777d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/76e777d87c73654ef1ac474c1d67d8499c05e7dc))

## v1.30.0 (2025-09-26)

### Bug fixes

* fix: remove coveralls ([`525a38b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/525a38b93fb656796b926da4003845eb6cf3823d))

### Features

* feat: Update dependencies and disable DB health checks

Bumped versions for asgiref, boto3, phonenumbers, uvicorn, and ruff in pyproject.toml. Disabled database connection health checks and atomic requests in settings.py by setting CONN_HEALTH_CHECKS and ATOMIC_REQUESTS to False and CONN_MAX_AGE to 0. These changes may be for local development or testing purposes. ([`667d89b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/667d89b791e6ca3ef11035c2a06a40fb2c707057))

## v1.29.2 (2025-09-25)

### Bug fixes

* fix: Increase image preview size and update serializer fields

Raised the max-height of image previews in the blog admin from 40px to 100px for better visibility. Updated BlogPostSerializer to set 'required=False' for 'reading_time' and 'content_preview' SerializerMethodFields, making them optional. ([`295f707`](https://github.com/vasilistotskas/grooveshop-django-api/commit/295f7072b6c93e1c221f5640ebde9547638b2dc3))

## v1.29.1 (2025-09-25)

### Bug fixes

* fix: missing migrations ([`503ffb6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/503ffb66af3bd224f471164701c5a10551859dda))

## v1.29.0 (2025-09-25)

### Features

* feat: blog API ordering and filter schema generation

Removed hardcoded OpenAPI ordering parameters from blog views and centralized ordering parameter schema generation in CamelCaseOrderingFilterExtension. Updated blog post comment filtering to use BlogCommentFilter and improved error handling. Added UserWriteSerializer for user updates with email and username uniqueness validation. Updated translations and improved code consistency across views. ([`999990d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/999990d3bedde2ed1e23a787ed01325ab2aa65f2))

## v1.28.0 (2025-09-23)

### Features

* feat: Add content_type and slug fields to serializers and schema

Introduces 'content_type' and 'slug' fields to BlogPost and Product serializers, MeiliSearch result serializers, and OpenAPI schema definitions. Updates dependencies: boto3 to 1.40.36, django-unfold to 0.66.0, and coverage to 7.10.7. Adds meilisearch-mcp service to infra.compose.yml for extended search capabilities. ([`1ca7115`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1ca71151d4af578483147b01eb9db0d09c0b7fef))

* feat: Add cursor parameter for pagination to API schema

Introduces a 'cursor' query parameter for pagination in the OpenAPI schema and view definitions. Updates core/api/views.py to define and include CURSOR_PARAMETER in BaseModelViewSet, and updates schema.yml to document the new parameter across relevant endpoints. ([`c81b071`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c81b071cd99242dcb3f3edfa56926e0be8df40b4))

## v1.27.0 (2025-09-22)

### Features

* feat: Refactor serializer config and logging, update tasks

Refactors API view and schema configuration to use explicit request/response serializer configs, replacing the previous 'serializers' dict pattern across all relevant views. Adds new logging configuration for Docker and Kubernetes environments, introduces a HostnameFilter, and creates a core/logging.py utility. Updates the log cleanup Celery task to only run in Docker development, renames it, and improves file selection logic. Adjusts Celery beat schedule to conditionally include the new log cleanup task. Updates product favourite serializers and schema to simplify product fields and response structure. Adds Twisted to dependencies and makes minor schema and test updates. ([`5e6d24e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5e6d24e0595e87ed89794258f650cae6c30dd14e))

## v1.26.0 (2025-09-20)

### Bug fixes

* fix: Update translations and pagination docs; improve favourite logic

Updated German and Greek translation files with new and revised messages, including support for tag filters and ordering fields. Improved pagination documentation for clarity. Enhanced favourite product serializer and view logic. Updated schema and search serializer for consistency with new API features. Adjusted unit tests to reflect these changes. ([`2fbe52a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2fbe52a9a300b2e2b832dea34f67a5f92f3b27c1))

### Features

* feat: Add flexible API pagination and update dependencies

Introduces support for multiple pagination strategies (page number, cursor, limit/offset) across all list endpoints, with new query parameters and documentation. Refactors pagination logic, updates OpenAPI schema, and improves serializer and view handling for pagination. Removes unused blog comment endpoint, enhances product review serialization, and updates various factories for unique image filenames. Upgrades several dependencies and MeiliSearch version, and adds a management command to clear MeiliSearch indexes. ([`86c85d3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/86c85d3b15affe18a0444012d5d112bf3d80d5dc))

## v1.25.0 (2025-08-18)

### Bug fixes

* fix: Make social profile fields nullable in UserAccount

Updated UserAccount model and migration to set social profile URL fields (twitter, linkedin, facebook, instagram, website, youtube, github) as nullable. Adjusted OpenAPI schema to reflect nullable fields and removed explicit blank/null enum choices. Also updated dependencies: boto3 to 1.40.8 and django-unfold to 0.64.2. ([`a7f39f1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a7f39f190f68611c728cb494b3557659f914fc1e))

### Features

* feat: Extend username validation and allow blank phone

Introduces ExtendedUnicodeUsernameValidator to support '#' in usernames and updates model, migration, and OpenAPI schema accordingly. Also allows phone field to be blank or null in authentication serializer and schema. Dependency versions updated in pyproject.toml. ([`3d5dbfe`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3d5dbfe8c8533abd70f1695847633d3d89e038df))

## v1.24.0 (2025-08-12)

### Bug fixes

* fix: update uv.lock ([`0fede2e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0fede2e7083b4c68eec6ab311ec6784291a66dbc))

### Features

* feat: Update uv version to 0.8.9 and CI workflow

Bump uv version from 0.8.0 to 0.8.9 in Dockerfile and dev.Dockerfile. Update GitHub Actions workflow to use setup-uv@v6, add workflow_dispatch trigger, and set UV_VERSION and FORCE_COLOR environment variables for improved CI configuration. ([`0927451`](https://github.com/vasilistotskas/grooveshop-django-api/commit/092745157e02060294020701f6a3be7708452d80))

## v1.23.1 (2025-08-12)

### Bug fixes

* fix: Add ordering OpenAPI parameters to API endpoints

Introduces explicit 'ordering' query parameters to various API endpoints in blog, product, and user views, enhancing schema documentation and client-side sorting capabilities. Refactors filterset class selection logic and updates schema.yml to reflect new ordering options for endpoints. ([`b1df40d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b1df40dba3a493af0e2b846f373fdc980147015a))

## v1.23.0 (2025-08-11)

### Bug fixes

* fix: seen_today test to use fresh data

The test_seen_today method now creates new Notification and NotificationUser instances with current timestamps, ensuring the test is independent of pre-existing fixtures and more robust. ([`e0cff5e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e0cff5e30cafbaea1d6002eed645391e9549cfc3))

* fix: Update uv.lock ([`43d1f0d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/43d1f0deb7feb934b6ae42784b5770439ca7a084))

### Features

* feat: Add full and short name getters to UserAccount

Introduces get_full_name and get_short_name methods to the UserAccount model for easier access to user's full name and username. ([`1ed3b45`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1ed3b457cc98268ecd60e7930c5ec5c6930f11be))

* feat: Add tag API endpoints and OpenAPI schema improvements

Introduces tag-related API endpoints, filters, views, and serializers, along with integration and unit tests for tag functionality. Enhances OpenAPI schema generation to support string query parameters, adds language parameter support to translation-enabled endpoints, and improves paginated response schemas for all pagination types. Updates core URLs to include tag endpoints and refines serializer schema documentation. ([`844824e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/844824ecdd84255e3de346c7aaf6f2a9e998f245))

## v1.22.0 (2025-08-08)

### Bug fixes

* fix: Improve cache detection and update translations

Enhanced cache disabling logic in cache_methods to better detect test environments, including pytest. Updated German, Greek, and English translation files with new line references and metadata. Fixed test assertion message in order payment view test and removed unnecessary blank line in user address filter test. Added .auth-token to .gitignore. ([`8651208`](https://github.com/vasilistotskas/grooveshop-django-api/commit/86512089ed8e839f7f230c1e15313f0863bbbf83))

### Features

* feat: Refactor filters, tests implemented and Bump Versions ([`20bfda6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/20bfda6e0842addd4296919dcc17dbe79b8b5b97))

* feat: Add custom discount action and improve dark mode UI

Introduces a custom discount admin action for products with a dedicated form and template, replacing fixed-percentage discount actions. Updates various admin and frontend templates to use 'dark:bg-gray-900' for improved dark mode consistency. Also updates dependencies in pyproject.toml and adds a new product/forms.py for the discount form. ([`7262a90`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7262a90e5506720b4e6735060b37d59e64890428))

## v1.21.0 (2025-07-15)

### Bug fixes

* fix: Clear BlogTag objects before name filter test

Added deletion of all BlogTag objects at the start of the test_filtering_by_name method to ensure a clean state for the test. This prevents interference from existing tags and improves test reliability. ([`a547fdf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a547fdf1b6e7db3c7e4c7546578baba8c76b554f))

* fix: Update admin list display tests and blog tag search

Removed 'id' from expected list display fields in admin tests for contact, pay way, user, and vat modules to reflect updated admin configuration. Changed blog tag view test to use 'search' parameter instead of 'name' for filtering tags. ([`07a8523`](https://github.com/vasilistotskas/grooveshop-django-api/commit/07a85235bf161a0a61ffb8b0ff013e0f0a7f7104))

### Features

* feat: Bump UV, remove "id" from admin `list_display ` and some admin classes renamed ([`f9dc562`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f9dc562b371c7c662e98ca6b1ab44cce29409e1a))

* feat: Add more tests ([`9f1398d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9f1398dc2e14e15f175c7278a9870b47c13dfa38))

## v1.20.0 (2025-07-11)

### Bug fixes

* fix: CI test failure in profiler tests

- Fixed timestamp calculation issue in test_add_memory_snapshot and test_add_slow_query
- Issue occurred when hardcoded start_time (1000.0) was greater than time.perf_counter() in CI environment
- Added @patch('time.perf_counter') to mock consistent time values
- Used assertAlmostEqual for floating point precision tolerance
- Ensures tests pass consistently across different environments ([`2a988cd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2a988cdaa76cb3c7ebd6b3b5f8033752f55266a5))

### Features

* feat: New tests, Bump Versions, pytest settings fixes ([`3ac7795`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3ac7795d4a5e0b54613568f337473a7c2e6b9dde))

## v1.19.0 (2025-07-09)

### Bug fixes

* fix: `ProductCategoryFactory` translations ([`b049fd3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b049fd3d6b11f3039a63d44e39aea64f6833d48f))

### Features

* feat: Refactor category factory and update type checking config

Simplified logic in ProductCategoryFactory for checking translation needs and reformatted argument placement. Removed mypy configuration from pyproject.toml ([`0a01f04`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0a01f047537cb1d91cde56427514eb1f80bee873))

## v1.18.0 (2025-07-09)

### Features

* feat: Bump Versions, improve factories and seeding ([`e8a0264`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e8a0264caf0ecce3574d33361e4b701b793c6e72))

## v1.17.3 (2025-07-05)

### Bug fixes

* fix: factories auto_translations set False ([`e8d7254`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e8d7254c9c173b0c1cae12d88c56e0ca32550958))

## v1.17.2 (2025-07-05)

### Bug fixes

* fix: Update Dockerfile ([`f559b67`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f559b674a583c44917cb4edbe455c9d0ffcc5818))

## v1.17.1 (2025-07-05)

### Bug fixes

* fix: update uv.lock ([`f6d80dc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f6d80dc7c636fe6971b1ae38cd849bbb31ee7989))

## v1.17.0 (2025-07-05)

### Features

* feat: Update pytest and coverage ([`86ab3fa`](https://github.com/vasilistotskas/grooveshop-django-api/commit/86ab3fa1530e9ba9386bd0bb2e6d3ba152131cc5))

* feat: type improvements, Bump Version and bug fixes ([`cfc17c9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cfc17c998727ea40e95855142ea9e36b21d5afe0))

## v1.16.0 (2025-06-27)

### Bug fixes

* fix: tests ([`34e9a38`](https://github.com/vasilistotskas/grooveshop-django-api/commit/34e9a38b2f77c024e230d0e6a917a06f70ffb484))

* fix: lint ([`416bf75`](https://github.com/vasilistotskas/grooveshop-django-api/commit/416bf75cf009b1765223b96b156366faff1121c6))

* fix: product signals, test fix ([`308dc95`](https://github.com/vasilistotskas/grooveshop-django-api/commit/308dc9514b2ba8d8703a38c40a69208c032046cb))

* fix: Product category `__str__` fix ([`916146b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/916146bef12b0ae458b4fc4a3662eac1c631570b))

* fix: lint ([`651f5b1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/651f5b125168e5931c36029b3b27e541f1497e79))

* fix: test_str_representation ([`4641ade`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4641ade0fb2fb81b60b399182c31109fa00108e0))

* fix: test_str_representation ([`49357f0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/49357f0c920f4db2848d0a74b6e8013cab85e53b))

### Features

* feat: Seed all command rewrite

- New `core\utils\dependencies.py`
- New `core\utils\profiler.py` ([`b2201b4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b2201b4bc9ba41609b5d6fbec16af6a2ca3c3cd2))

## v1.15.0 (2025-06-25)

### Features

* feat: Improve admin with `unfold`, Bump Versions ([`60b85d8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/60b85d8ae29f4fe25fc4d6b04215d3c8614ea5a4))

## v1.14.0 (2025-06-22)

### Features

* feat: Include more tests, added managers, added filters, split cart and cart item, ProductCategoryImage and more ([`c93cc31`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c93cc31ef74779091ad868d8ae15a2d8b295db66))

* feat: Remove vat API views, remove tip app, remove slider app, refactor API endpoints and schema and Bump Versions ([`e626219`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e6262191c133b2ad456afe5114aebac9569d3be0))

## v1.13.0 (2025-06-07)

### Bug fixes

* fix: remove useless tests ([`8d40865`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8d40865308f4ae0258d5c8b12ff8fae8a8277d78))

### Features

* feat: postgres specified version, tasks improved and Bump Versions ([`7b17681`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7b176810cc7fec3ac7e4c0ff044922eb4491c85b))

## v1.12.0 (2025-06-04)

### Chores

* chore: remove useless files ([`50526fb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/50526fb47da4186472d8513e6a64cf0a181e73ba))

### Features

* feat: Increase postgres pool, Bump Versions ([`1c35327`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1c353276d440056d6de457cdcb7a5393da3faf5d))

## v1.11.0 (2025-06-01)

### Features

* feat: Improve API schema docs, generate locales and Bump Versions ([`d6c88a1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d6c88a17d23751c8e6c59f05ea1d29d795f87e8f))

## v1.10.0 (2025-05-31)

### Features

* feat: Update user address API view, Cart refactor to use headers instead of session, Remove `@override` decorators, update API docs, blog API filters, ([`d73fa58`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d73fa5899cfb1dd14db761ec0a1e948ba2c4bc55))

## v1.9.0 (2025-05-24)

### Features

* feat: `ci.yml` build wheel with `--installer uv`, added `session_key` at cart serializer, app structure improvements, payment api view serializers implemented ([`a32a366`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a32a36626787a5ca50bb2b4d33752f317f88eef3))

## v1.8.1 (2025-05-23)

### Bug fixes

* fix: remove [build-system] ([`c5806a0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c5806a0cbbae01000283a77af027d39fb8dc6063))

## v1.8.0 (2025-05-23)

### Features

* feat: Update `pyproject.toml` and `ci.yml` fix ([`3c0aa56`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3c0aa5695f4dcde8c2aa890ea43d5c1dc7f48c03))

## v1.7.1 (2025-05-23)

### Bug fixes

* fix: error exposure ([`839993b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/839993b71c6160104cbafb3691b3f984a1847bd8))

## v1.7.0 (2025-05-23)

### Features

* feat: remove `thumbnail` fields, ImageAndSvgField refactor, github ci permissions added, remove useless stuff, ([`14c440e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/14c440ed8f85726a16cbaa821baea33e4c6136c2))

* feat: Optimize Dockerfile ([`d20369f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d20369f8415ad2ff6af74a0120e062fa33f86c80))

## v1.6.0 (2025-05-21)

### Bug fixes

* fix: test fix ([`b05c890`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b05c8904c76346510e610553bff309b5053a0083))

### Features

* feat: Use django daphne ([`fcb1cf7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fcb1cf74d79baa5c1670a0260a0356f5c9532041))

## v1.5.0 (2025-05-21)

### Features

* feat: Remove `image_tag`, docker updates ([`e0cb53e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e0cb53edcf7195f2d4d72a3deb2ca297ce99badd))

## v1.4.6 (2025-05-17)

### Bug fixes

* fix: ci ([`a3643bd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a3643bd6f9f9031707e131d98f16759508c17eeb))

## v1.4.5 (2025-05-17)

### Bug fixes

* fix: ci ([`4d8d587`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4d8d5871704c5b80a2e6a182111c63a4891e3de2))

## v1.4.4 (2025-05-17)

### Bug fixes

* fix: ci ([`c2bc11f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c2bc11f6d9f931ed2591c4f9703edc0d815f5d91))

## v1.4.3 (2025-05-17)

### Bug fixes

* fix: ci ([`c5ab944`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c5ab944172c845d02f7b9fd033bfe0cd3f020f05))

## v1.4.2 (2025-05-17)

### Bug fixes

* fix: ci ([`3999706`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3999706be4f14e621d581e668775d0a26debe680))

## v1.4.1 (2025-05-17)

### Bug fixes

* fix: uv sync ([`b03c255`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b03c255a097941da345114bece91a85c92e0c5eb))

* fix: ci ([`84bc944`](https://github.com/vasilistotskas/grooveshop-django-api/commit/84bc944ef11e0955d765d914fc0b633dc136c552))

## v1.4.0 (2025-05-17)

### Bug fixes

* fix: find_packages at setup.py ([`e13e7aa`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e13e7aa3f90c014e2b274f98d618ee406f964617))

* fix: ruff format ([`1f8d547`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1f8d54744b4372c14beaae743470fa1c078ded43))

* fix(tests): increase stock at order test ([`8806e37`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8806e372d6e17a361d48b727d8a1759d4ed0a578))

* fix: ci ([`5548e1b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5548e1b91d286027042317aa237d789cb59ac1fc))

* fix: ci ([`5b4372e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5b4372e498abb85e7f6f77a45122c6d1afd74c55))

* fix: ci ([`d1fdfd5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d1fdfd575a818ee5354d558880b7d94490c98916))

* fix: ci ([`a3121ed`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a3121ed9f7fbe76893be9529f55a0e5560efa8c2))

* fix: ci ([`38948aa`](https://github.com/vasilistotskas/grooveshop-django-api/commit/38948aa22e4a9fd0c45da051f517de51c22bdc71))

* fix: ci ([`6565aee`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6565aee50410eabcba1ae229514d3ed7a8f31753))

* fix: add python-version file ([`0997afc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0997afc595d758b706dead787acf436059f20576))

### Features

* feat: replace poetry with uv ([`683bfb6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/683bfb6671695e90fe9d0d6832f002d916c2ed2e))

## v1.3.0 (2025-05-17)

### Features

* feat: compile messages and api schema, ci release check to run only at main branch and pay way admin fix ([`7229ca6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7229ca60ab5bec0bf71b293bfcf736095a1ff096))

## v1.2.0 (2025-05-17)

### Bug fixes

* fix(tests): Increase popular_post view count ([`bc2a42f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bc2a42f2ff2f2c99c71be84df63dd0c693bd3f58))

* fix: pre-commit run ([`8430ba7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8430ba79fef9350e46180415df78ffc9c8f936d3))

* fix: pre commit run ([`c869e28`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c869e2849d74ecefd79c9328cd8a50492b15ea00))

* fix: remove useless comments and prints, test fixes ([`392d802`](https://github.com/vasilistotskas/grooveshop-django-api/commit/392d80239082cf0d6faffd88d06841feaf31e325))

* fix: metrics update ([`b5109b5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b5109b5a1e6057f733db39f1ad308db6d305a698))

### Chores

* chore: update poetry lock ([`afd5f4d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/afd5f4d3a74b7a0f81d06d4456756a5e3aba9cfe))

### Continuous integration

* ci: Increase testing timeout ([`bfb27a1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bfb27a18fed2f3822742ae23f151075b521bcc07))

### Features

* feat: Improve tests performance 8s->2s ([`d79a99d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d79a99d46e7fb1791d0983650e4f1fee0e5aefe7))

* feat: Pay way app improved ([`27442f1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/27442f11043b7e81ddf7636e7b31b23a43350275))

* feat: Rewrite order, general improvements

- SUPPORT_EMAIL rename to INFO_EMAIL ([`199eec0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/199eec08384ad1ebd705d61ce862894cf5f69571))

* feat: Remove `status` field from blog post, factory and admin panel fixes ([`d21076a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d21076a91af5f49367067e6a521a87c315d32d6c))

* feat: Update model indexes and blog post filters ([`1ae899e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1ae899e57929118156973c9e015510f3a2125b54))

* feat: progress ([`479b813`](https://github.com/vasilistotskas/grooveshop-django-api/commit/479b813df7844b3f7cf1265f81bad570653b3f89))

* feat: Init prototype ([`89f3915`](https://github.com/vasilistotskas/grooveshop-django-api/commit/89f391566a2e311204a35a8c132ddff0711b3f17))

## v1.1.0 (2025-05-05)

### Features

* feat: Bump Versions, Admin panel enhance ([`91125d2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/91125d2255f32c7a4fc4b48fc3f60a38248b241c))

## v1.0.1 (2025-05-02)

### Bug fixes

* fix: cart migration ([`7f6b37d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7f6b37db08ed909026d9882ed9b4e6b8bf40f5dc))

## v1.0.0 (2025-04-30)

### Bug fixes

* fix: cart factory ([`8862002`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8862002c883232b9b8ceca732697c05f63b3ed69))

* fix: cart session_key ([`1252e88`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1252e88af6e8dd07723cabc4f214da7e63013a26))

* fix: cart session_key migration ([`0eafd57`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0eafd5760c726eb21532fa4529e8c5892706c313))

* fix: ruff format ([`dbae591`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dbae5914485d5a12417d1c87dd1745fe5fbcc5c8))

### Features

* feat(core): implement guest cart functionality and system-wide improvements

* cart: add session-based guest cart support with new session_key field
* admin: integrate UnfoldAdminSite and optimize admin views
* logging: replace custom LogInfo with Python logging module across multiple components
test: add comprehensive tests for guest cart functionality
build: upgrade multiple dependencies including poetry, django-unfold, and celery
docs: update API documentation and schema.yml for cart endpoints

BREAKING CHANGE: Cart model schema updated with session_key field ([`eeec158`](https://github.com/vasilistotskas/grooveshop-django-api/commit/eeec1586d473a77065abf15fdae31aa6e878ac93))

## v0.199.0 (2025-04-16)

### Features

* feat: Bump Versions ([`65abdce`](https://github.com/vasilistotskas/grooveshop-django-api/commit/65abdcee612de114129b5263e1f5835b53cecdff))

## v0.198.1 (2025-04-12)

### Bug fixes

* fix: revert tailwind ([`4ab08a8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4ab08a8d33b1ce0b27e2305b87a497c1d25f5944))

## v0.198.0 (2025-04-12)

### Features

* feat: unfold ([`8b60df4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8b60df42abe7d60de8a65b6440bbe4603d72fad6))

## v0.197.0 (2025-04-12)

### Chores

* chore: rename service to services ([`8a6c3a9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8a6c3a9114284d106b5887c6d30a7037bbc33189))

### Continuous integration

* ci: update pg defaults ([`bc4569a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bc4569a5713ebea28313ee249352469447466b01))

* ci: Added ruff hook at `pre-commit-config`, lint fixes and cleanup compose.yml ([`0a3f1bf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0a3f1bf87b9cb1a1c1cbbe711141d028b5fb3aad))

### Features

* feat: Remove expand functionality ([`d859631`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d85963182925c9ab2183a87711b40913b0f7b344))

## v0.196.1 (2025-04-05)

### Bug fixes

* fix: static url ([`8b876d3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8b876d39285870663858f7f186d35e46adda5885))

## v0.196.0 (2025-04-05)

### Bug fixes

* fix: pyproject.toml ([`8609e78`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8609e789c00889c86d2dac3901daea7f56acc278))

* fix: pyproject.toml ([`ca89963`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ca89963da0209dd8e9fac541a7fa8c3b325f0e9a))

### Features

* feat: Bump Versions

- Add AI related files to .dockerignore and .gitignore
- Update pyproject ([`c7a3e1e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c7a3e1e3a7df7de0d9b66bcdef6f0f403742097a))

## v0.195.1 (2025-03-31)

### Bug fixes

* fix: revert tailwind ([`6e54cab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6e54cabceeb1936bff422ea88d5818678c620802))

## v0.195.0 (2025-03-30)

### Bug fixes

* fix: poetry lock ([`adfff33`](https://github.com/vasilistotskas/grooveshop-django-api/commit/adfff33c045a294620902ef56133fff221b9221e))

### Features

* feat: Bump Versions, tailwind 4, notifications fixes ([`dad42cb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dad42cb64f082dfbd6024756a23e920b7aec8dff))

## v0.194.2 (2025-03-24)

### Bug fixes

* fix: update dockerfile ([`7c76d45`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7c76d45541f56c13ebdfefe051ca1448f8329892))

## v0.194.1 (2025-03-22)

### Bug fixes

* fix: update dockerfile ([`d5d9956`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d5d99568629b2a3ec6ef357bb59bc3e87031cfba))

## v0.194.0 (2025-03-20)

### Bug fixes

* fix: Dockerfile staticfiles dir ([`bd8562b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bd8562b0f70026fc4ab0f0ffd71b54b78568a9df))

### Features

* feat: Bump Versions, fix Dockerfile staticfiles dir ([`8d94cee`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8d94cee97ac1df8cb083d469d114503dd4010edf))

## v0.193.1 (2025-03-15)

### Bug fixes

* fix: tests ([`4d67a9a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4d67a9ae9e0c7848303a85f5246ee1ac75290ca5))

* fix: get_or_create_cart, github docker ([`4ae26a6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4ae26a62eabc49a9bc74095be1c99d938f3ec308))

## v0.193.0 (2025-03-15)

### Features

* feat: Bump Versions, docker updates, lint and type updates ([`f45a7fc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f45a7fc316c1e7d6828543e18702f035ffbed56c))

## v0.192.0 (2025-02-22)

### Features

* feat: Bump Versions ([`5bb3b89`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5bb3b899db2aa7be2f4606966e877b36567ad785))

## v0.191.0 (2025-02-15)

### Bug fixes

* fix: revert celery app ([`40c998a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/40c998ade7a4ddaec5727d80973e484aacd50fc5))

* fix: ruff ([`2c1c671`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2c1c671a501e5129d2ff3a22d0eb20e0462fa838))

* fix: ACCOUNT_AUTHENTICATION_METHOD rename and celery import ([`73782c9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/73782c932a123f890eb7353318fec5773a4e9d38))

### Features

* feat: Bump Versions ([`868e1b0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/868e1b03fe705b7635fa4faa9dbed2942cf5a48f))

* feat: Bump Versions, celery init change ([`2f7637e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2f7637e0c8a1e295fa7ad76424dd082552fcbdf0))

## v0.190.0 (2025-02-11)

### Features

* feat: Bump Versions ([`70eee48`](https://github.com/vasilistotskas/grooveshop-django-api/commit/70eee4895a121f0caa7e6f44cb4f519579c5fc49))

## v0.189.0 (2025-01-21)

### Features

* feat: Bump Versions ([`b6bafa8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b6bafa8460e1562e64911580e07b41998a9df30d))

## v0.188.0 (2025-01-13)

### Features

* feat: Bump Versions ([`8d8a4ee`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8d8a4ee67dada41e284375c78da45c25ecb8de11))

## v0.187.2 (2025-01-07)

### Bug fixes

* fix: add missing lib ([`59ff76f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/59ff76fbfe38ece61cfa6ac314197e3af7c2f208))

## v0.187.1 (2025-01-07)

### Bug fixes

* fix: add missing lib ([`a086f70`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a086f7025cf65ef74393fd61e87b5a9ab5a962ac))

## v0.187.0 (2025-01-07)

### Features

* feat: Bump Versions ([`d737557`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d737557440345257851d8302641bb5e98c822c85))

## v0.186.0 (2024-12-31)

### Bug fixes

* fix: poetry lock ([`3a20708`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3a207084eac12749f802e32b22fcb1237d1203a3))

### Features

* feat: Bump Versions, replace flake8,black and isort with ruff ([`43eaa58`](https://github.com/vasilistotskas/grooveshop-django-api/commit/43eaa58c25de243b15221695cf023256444d1eee))

## v0.185.0 (2024-12-08)

### Bug fixes

* fix: session test patch env values ([`5bc874d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5bc874d4a78624c409c1479754eeb709cfb901c2))

* fix: remove qodana ([`2d06f0b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2d06f0bbfc98c86e9be67028af26371bb875e4a6))

### Features

* feat: Remove gzip compression asgi, Cart improved, Cache improved, Bump Version ([`8386bf0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8386bf04a1692fea902f38790550890dded0a7d0))

## v0.184.0 (2024-12-04)

### Features

* feat: Bump Versions ([`8ee134f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8ee134f01478b07456cb3eac436a2e34457d62e3))

## v0.183.0 (2024-11-29)

### Features

* feat: bump meili version ([`f735bff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f735bffa27266e16fcf7cc27eb2d0801a126221b))

## v0.182.0 (2024-11-28)

### Features

* feat: Bump Versions, Improve `cart` app, remove `compress-old-logs` task and meilisearch queryset improve ([`be3c247`](https://github.com/vasilistotskas/grooveshop-django-api/commit/be3c2472b7056f1e8a4bd7a73f03d78a8a73f2b4))

## v0.181.0 (2024-11-22)

### Features

* feat: Bump Versions, remove `run.py` file, update `.dockerignore` and entrypoints for dockerfile ([`5beefe6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5beefe61eeae99f6e7484c7f3dfd7b538e9d73d9))

## v0.180.0 (2024-11-20)

### Features

* feat: Bump Versions ([`3aa27c4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3aa27c49c7a212342dbb95a9c32fbc15c453b227))

### Refactoring

* refactor(cache): `CustomCache` class improved ([`d387782`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d387782c63ce5f9638e6443489e8a5b81a8d6b6d))

## v0.179.3 (2024-11-16)

### Bug fixes

* fix(Dockerfile): attempt ([`0782a70`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0782a704f3cd8bb8e1ca152145f050e76c971347))

## v0.179.2 (2024-11-16)

### Bug fixes

* fix(Dockerfile): attempt ([`fdea712`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fdea7121f1c1d065da7f2d8f580297666f368247))

## v0.179.1 (2024-11-16)

### Bug fixes

* fix(Dockerfile): attempt ([`45e32c3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/45e32c3685c2ed48ce7f48554b5229768ae374d6))

## v0.179.0 (2024-11-16)

### Features

* feat(ci): Updaste python to 3.13 and use `slim-bookworm` image ([`1986853`](https://github.com/vasilistotskas/grooveshop-django-api/commit/19868533704237d6055693331cdff2388261f7fb))

## v0.178.0 (2024-11-16)

### Features

* feat: Bump Versions ([`3e01ca3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3e01ca36eb11defe3f294e3f9a1b573b17a4f1a2))

## v0.177.0 (2024-11-13)

### Features

* feat: Bump Versions ([`0cf3d5f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0cf3d5f7407f1ba7eca7a9e239e45c160947f7f7))

## v0.176.0 (2024-11-09)

### Features

* feat: csp policy package `django-csp` ([`568d986`](https://github.com/vasilistotskas/grooveshop-django-api/commit/568d98692fbf6b69745926f7f21a6d8acfbeda05))

## v0.175.0 (2024-11-09)

### Features

* feat: Bump Versions ([`2371029`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2371029a266d8551d89b4906a0902bc496966eaa))

## v0.174.0 (2024-11-08)

### Features

* feat: API schema fixes, NonceMiddleware implemented rosetta base template override and Bump Versions ([`521e3f0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/521e3f026303cded368391e09f4388e9a653d954))

## v0.173.0 (2024-10-26)

### Features

* feat: Robots txt added ([`ffc560c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ffc560c418330928cfbdeb50fe1872454d31706e))

## v0.172.0 (2024-10-26)

### Bug fixes

* fix: remove useless test ([`0d7e6a4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0d7e6a45369ae24b0d6f7c0494a7279a884b23fe))

### Features

* feat: remove `unicode` and improve related blog posts ([`00b34e7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/00b34e7aec4c2894502acb62f34e257a4ca61d82))

## v0.171.1 (2024-10-26)

### Bug fixes

* fix: revert setting ([`fa6b715`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fa6b715b7fdd34877557c4b16a61aae0e10db8dd))

* fix: use TranslationsForeignKey in Translation models ([`bbe0b75`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bbe0b75f068ab63e4c9177b9024b9c7677e8e77c))

## v0.171.0 (2024-10-25)

### Bug fixes

* fix: remove useless test `tearDown` ([`60a4531`](https://github.com/vasilistotskas/grooveshop-django-api/commit/60a4531feeb84bb29d5ffed460672c91917d5e1a))

### Features

* feat: related blog posts ([`578ae0d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/578ae0dac6d869afbde83fb930c0138e9939ab88))

## v0.170.0 (2024-10-24)

### Bug fixes

* fix: Slider factory title max length ([`80bcc12`](https://github.com/vasilistotskas/grooveshop-django-api/commit/80bcc12ba01dfdda5acfbb2ef5b8b0717d3a2e15))

* fix: Slider factory title max length ([`f2214ff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f2214ff8d0c30d1fd8333ba754702636e53ffb13))

* fix: Slider factory title max length ([`66f9471`](https://github.com/vasilistotskas/grooveshop-django-api/commit/66f9471dd7c57fb28d03dd5c4f3aa0ead5ef7638))

### Features

* feat: Implement `MaxLengthFaker` and usage in slider factory ([`9bba342`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9bba342be5db77ced8afd6e95ea6ee5ba69e4cbf))

* feat: Bump Versions, lint fixes and implement `get_or_create_instance` helper method ([`f45d2da`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f45d2dafe9aa515305dab095c0f3585a3fd8bad7))

## v0.169.0 (2024-10-16)

### Features

* feat: Bump Versions ([`bb853f6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bb853f65bc539db6a111e44aeb2f71c28351b27d))

## v0.168.0 (2024-10-10)

### Bug fixes

* fix: lint fix ([`5f84aa6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5f84aa6542825d144bb19c84dffdb670e0407e17))

### Features

* feat: Bump Versions and lint fixes ([`931ec0f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/931ec0f86d2d4f5ba4caa09a798800a686e27621))

### Unknown

* Add github workflow file ([`7576a20`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7576a20e4c88e0fd8d23e1b44b055c1148197be3))

* Add qodana.yaml file ([`5e1a6e6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5e1a6e6b8f744e7c00491ec04e4eee057e9542e8))

## v0.167.0 (2024-10-08)

### Features

* feat: Bump Versions ([`cc1b469`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cc1b4696569c285f46cf7bbbd71cee7338026549))

## v0.166.0 (2024-10-03)

### Features

* feat: Bump Versions ([`2856495`](https://github.com/vasilistotskas/grooveshop-django-api/commit/28564953ddba2ad4bf1241af7e257d29477d7f6c))

## v0.165.0 (2024-10-01)

### Features

* feat: Bump Versions ([`01d929f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/01d929f360876241a9872a2c588b0d9952938d15))

## v0.164.0 (2024-09-24)

### Features

* feat: Bump Versions and remove useless template overrides ([`788cff6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/788cff6e2368dd74b6ec0466872ea2e2f8ed19bb))

## v0.163.1 (2024-09-22)

### Bug fixes

* fix: Include missing account email templates ([`d9dd09d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d9dd09dc6ba33cef1e205347f339e133cf56214c))

## v0.163.0 (2024-09-22)

### Bug fixes

* fix: ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED true ([`2c45dfe`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2c45dfe9083ec162a85c50712424a45d817d0d16))

### Features

* feat: Bump Versions ([`1af8e15`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1af8e15c08d6c3c5a4da56207601292a94c796c0))

## v0.162.2 (2024-09-21)

### Bug fixes

* fix: remove unused `django.contrib.humanize` and CSRF_COOKIE_SAMESITE set to `lax` ([`c814b1e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c814b1e87e745591e30068cb49ba7e8b4c6cebe4))

## v0.162.1 (2024-09-18)

### Bug fixes

* fix: upload_image ([`f4bda52`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f4bda525c0910bc7efc732d61bd579f179961698))

## v0.162.0 (2024-09-18)

### Features

* feat: SECURE_SSL_REDIRECT from env ([`76210fb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/76210fb21e67749e932d2e97755a570caa8a3532))

## v0.161.6 (2024-09-18)

### Bug fixes

* fix: revert / in admin url ([`0425a4b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0425a4bd2f5c10f59ce13acb2eab08c55c9fc5d7))

## v0.161.5 (2024-09-18)

### Bug fixes

* fix: production STATIC_URL and MEDIA_URL fix ([`5e0c650`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5e0c6506e2ba79947a60af3d1c7504cf5dec927f))

## v0.161.4 (2024-09-18)

### Bug fixes

* fix: SECURE_SSL_REDIRECT to false for now ([`c4bf5fd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c4bf5fddfb9e682df8da1f5d5b93354ba7f48fa5))

### Unknown

* feat; static for production ([`f396c98`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f396c98c076ca6567e416496f48b0a2837eba46b))

## v0.161.3 (2024-09-17)

### Bug fixes

* fix: poetry lock ([`b6dfe5d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b6dfe5d35c17494358bd1273205600b510de5779))

* fix: remove compressor ([`cb1a7cd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cb1a7cdb2bc814f0f65cbd173af8b43c5992a25a))

## v0.161.2 (2024-09-17)

### Bug fixes

* fix: csrf http ([`761c71b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/761c71b0b956fa430af8c0d81594d318068733af))

## v0.161.1 (2024-09-17)

### Bug fixes

* fix: remove useless tests ([`7a44e3e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7a44e3e4ebc6c3e5d9aee030a76aa1dbceae3967))

* fix: remove useless tests ([`d4f7006`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d4f700637a85cfa773c148cbcbfd25dbe6d7d17f))

* fix: whitenoise ([`c874dbd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c874dbd7b003a90a00df9b22adb9fc76bff81553))

## v0.161.0 (2024-09-17)

### Bug fixes

* fix: whitenoise test ([`ee827e6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ee827e62392a33b34f53d843aa3ced52198bde6c))

### Features

* feat: whitenoise ([`add9ab1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/add9ab1f315f0a4f69cc2f7b20aa57c83b38e76c))

## v0.160.1 (2024-09-17)

### Bug fixes

* fix: url static and media path ([`28fc35c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/28fc35c318dd1bfaffc1114fa08990a77ba8b8cf))

## v0.160.0 (2024-09-17)

### Features

* feat: set CSRF_COOKIE_SAMESITE to `None` ([`bce89e0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bce89e011fd0cd87743819c64da651665958eafd))

## v0.159.0 (2024-09-17)

### Features

* feat: Custom admin enable and remove django_browser_reload ([`35666fb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/35666fb56e1de8f485efb9c54c03a9919ecec386))

## v0.158.1 (2024-09-17)

### Bug fixes

* fix: revert admin ([`04f130e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/04f130e4542a84c001e1f5bb1cc64589fa2d24ff))

## v0.158.0 (2024-09-17)

### Bug fixes

* fix: temporary ([`2ed504a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2ed504a378015e2282d8f0b37b39553cd89f51b0))

### Features

* feat: cache improvements and admin clear cache, ([`0188cac`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0188caca7b66f48a04b10ff4a11cb557b3d6b2b6))

## v0.157.0 (2024-09-13)

### Bug fixes

* fix: run poetry lock ([`c5ff0c0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c5ff0c0f5baced846d0c68c09938844827fefdf6))

* fix: run poetry lock ([`7bd01b6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7bd01b65f91783f1af26697445a601111794a51b))

### Features

* feat: Include `httpx` dep ([`20838ef`](https://github.com/vasilistotskas/grooveshop-django-api/commit/20838efa9b76e966683286bf7663fcd16c8369c1))

* feat: Bump Versions, health check API endpoint and `account/provider/callback` view implemented ([`108a2f1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/108a2f1982e72b8fb7e881ebb937c02ff9e33301))

## v0.156.2 (2024-09-09)

### Bug fixes

* fix: include missing API serializer fields ([`e7f858d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e7f858d65619b4f831736ffb6e794c1d55e580b8))

## v0.156.1 (2024-09-08)

### Bug fixes

* fix: add missing `change_username` api endpoint ([`d5336ff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d5336ffd649412a6532f03768f27f606331990ff))

## v0.156.0 (2024-09-08)

### Features

* feat: Bump versions ([`ec774d6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ec774d638974ed70767df2120ab79af13e3800d8))

## v0.155.0 (2024-09-05)

### Features

* feat: Bump versions ([`a5cde62`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a5cde62aaf902ccb8612a6344bbddb304b99d068))

## v0.154.1 (2024-09-02)

### Bug fixes

* fix: AWS usage in `upload_image` ([`65ee35a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/65ee35a0e4cf74acf80a8e8731fbe16375446642))

## v0.154.0 (2024-09-02)

### Bug fixes

* fix: update meili ([`9a1d329`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9a1d3292d7faca0c02f71515be08e0d39c3b7757))

* fix: update meili ([`7864452`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7864452faf9da68ae1e0310b89d11937f9f1cf1b))

* fix: update meili ([`d14ef4d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d14ef4d7d7ccb80a41e25b6ec2691cdeabe2c943))

* fix: update meili ([`441877f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/441877fee70b12f6bbe58d527e9335deb4b066b8))

* fix: update meili client ([`25b0ca9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/25b0ca9a62a197cd5f4d1dcf3f54899549b9f709))

* fix: update env ([`20eb3f8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/20eb3f8dc8d1c7e0ecbe2a00ae3721529b221b30))

* fix: update ci.yml ([`7b4529f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7b4529f2effbe84efdaefccce00050ddc95d208d))

* fix: update ci.yml ([`d8dc58f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d8dc58f84b51816a31c9d290381c5e6aad0631c8))

* fix: update settings ([`678cbab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/678cbab3dd2b4f4dc8612e41a71ecffc49d3575f))

* fix: fix meili ci ([`78dbc0d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/78dbc0d273d91aa8188b095b7390c5c24ead0c4f))

* fix: update default meilisearch host ([`16b37f1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/16b37f1d4bb3428be558ce4548e73baa20bc8d29))

### Features

* feat: update ci for meilisearch ([`a63b47d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a63b47d33000e953444c84bbef68006453e4a0f4))

* feat: Meilisearch, remove `main_image_absolute_url` and `main_image_filename` and create a single `main_image_path` ([`f19b88e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f19b88ea83dd65d0a7e52e3f9537318411108be9))

### Unknown

* Update ci.yml ([`b659300`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b65930052bc1994cc7fafdc05c7fb74349c04af0))

* Update ci.yml ([`54d74b7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/54d74b721e058315bd954acdcfd9f67dde2d6c99))

## v0.153.1 (2024-08-29)

### Bug fixes

* fix: use env `USE_AWS` in upload_image method for tinymce ([`7b7dc0a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7b7dc0a8237161c9fbb82ad941116a48d7cec29e))

## v0.153.0 (2024-08-29)

### Features

* feat: Bump versions ([`6efc135`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6efc1358c23faecb764659c296a3e04490fe0692))

## v0.152.1 (2024-08-27)

### Bug fixes

* fix: remove `docker` usage from env var `SYSTEM_ENV` ([`b3523f7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b3523f7e891fc3cea8dc39c00b9712e860ebc203))

## v0.152.0 (2024-08-27)

### Bug fixes

* fix: AWS usage configurable ([`2013f18`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2013f181e498389399499f2d4a6d6f16440c3ce9))

### Features

* feat: remove aws s3 first attempt ([`7ecaf6d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7ecaf6d51b482e293441a3f2d1ec30acd18cde19))

## v0.151.0 (2024-08-25)

### Bug fixes

* fix: test fix after logger update ([`6ed8fff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6ed8ffffa617459bbef5dede89014b9fea5519a1))

### Features

* feat: logger improve ([`16fed8f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/16fed8f47706221ebbbd79de884cebe352ea6f59))

## v0.150.0 (2024-08-25)

### Features

* feat: django compressor for static files ([`52740f3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/52740f3a19bf2e0b87c325dfbe908ab2899a4d1d))

## v0.149.2 (2024-08-25)

### Bug fixes

* fix: minify styles css ([`a2956de`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a2956de6491db4005749038d36577a53521b909f))

## v0.149.1 (2024-08-25)

### Bug fixes

* fix: correct CSS link in `account/base.html` and test ([`92943ef`](https://github.com/vasilistotskas/grooveshop-django-api/commit/92943ef3934bc6eb0b6b88c8fae39514d8a13d70))

* fix: production static file location fix ([`40f15fb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/40f15fb94ff7d80f344ac7a3caf7acb439b45c6a))

## v0.149.0 (2024-08-24)

### Features

* feat: Added expiry_date field in `Notification` model, task and command to clear expired notifications and model indexes improvements ([`1ba77c0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1ba77c00546d0f24e2a96c1809ce3d37fdd4d9c0))

## v0.148.0 (2024-08-23)

### Features

* feat: ManageTOTPSvgView update ([`aad0796`](https://github.com/vasilistotskas/grooveshop-django-api/commit/aad07965daa37e0bf526760aef85ff36eb5016bf))

## v0.147.0 (2024-08-22)

### Features

* feat: Improve API homepage UI, MFAAdapter get_public_key_credential_rp_entity override to set id ([`b26d5ba`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b26d5babf303e1ddbe75d61e38a2f0d53a9dd378))

## v0.146.1 (2024-08-22)

### Bug fixes

* fix: debug fixed ([`327793d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/327793d234f298eb8217d458411165ff222e50c7))

## v0.146.0 (2024-08-22)

### Features

* feat: Bump Versions and debugging ([`8ee4b1f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8ee4b1f557c50b5400ce6e1ff54fcce19970893e))

## v0.145.0 (2024-08-22)

### Features

* feat: USE_X_FORWARDED_HOST setting ([`1d185f5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1d185f502b27be978a9ae5a0180c1145e9852ea3))

## v0.144.1 (2024-08-21)

### Bug fixes

* fix: liker_user info at `notify_comment_liked` ([`aa2cb76`](https://github.com/vasilistotskas/grooveshop-django-api/commit/aa2cb76052af033b25671de510963ac9e733b38d))

## v0.144.0 (2024-08-21)

### Features

* feat: tasks for clear history. Bump Versions ([`93e43e2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/93e43e281c6a4fb9802122a1963574330704ba63))

## v0.143.0 (2024-08-21)

### Bug fixes

* fix: remove signal tests for a while ([`d696c62`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d696c62454dcfabb36f3dc16a7a80e47461ffd97))

* fix: run poetry lock ([`08f5cc3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/08f5cc3f85cd2d45dcafdb82604450851141acfd))

### Features

* feat: notification improvements ([`dbd05b5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dbd05b5e17fb739c13746cb120b25fe11533c5cb))

* feat: add `simple_history` and implement notify_product_price_lowered and post_create_historical_record_callback signals, Bump Versions ([`7b4a559`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7b4a559c7e221ec7af4cfd0e89e1c9dc1b34839b))

## v0.142.0 (2024-08-18)

### Features

* feat: factory improvements, useless tests remove and API serializer readonly fields ([`920b449`](https://github.com/vasilistotskas/grooveshop-django-api/commit/920b449b74c1d5de04be8e339e92095be152020c))

## v0.141.0 (2024-08-17)

### Features

* feat: Bump Versions and minor fixes ([`759dd18`](https://github.com/vasilistotskas/grooveshop-django-api/commit/759dd180ff0f9c443e218ee5407ccd85a0edb331))

## v0.140.0 (2024-08-10)

### Features

* feat: Bump Versions, config moved to settings and websocket notification for user implemented ([`26c7115`](https://github.com/vasilistotskas/grooveshop-django-api/commit/26c7115d23be359e6384659bb4c3066c4fc5de2b))

## v0.139.0 (2024-07-28)

### Features

* feat: Tag and TaggedItem factories implement ([`8b03ed7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8b03ed7f44e5ed7b309078ec32498db782110a2c))

## v0.138.0 (2024-07-28)

### Features

* feat: More at Description

- Factory for model improvements
- Factory seed command refactor, `sync` and `async` commands available
- Bump Versions ([`6d7fdb0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6d7fdb0bebe1dae633b4c4ff0462fd0a90c8f28b))

## v0.137.0 (2024-07-26)

### Features

* feat: More at Description

- Factories remove `_create` classmethod override and use `@factory.post_generation`
- Implement generic tag model and tagged item model
- Product tag and get tags API endpoint implemented ([`2b9c891`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2b9c891d1d1b7eb715993272aceb161215814f25))

## v0.136.0 (2024-07-20)

### Features

* feat: Bump versions ([`8395b02`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8395b020375ed1b9732a14d613b8cbfdc40b113f))

## v0.135.0 (2024-07-19)

### Features

* feat: Bump versions ([`c2b90d9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c2b90d9f15dea18a26f0d6a973b44ebf5c28a4c8))

## v0.134.0 (2024-07-18)

### Features

* feat: Bump versions ([`04bc57c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/04bc57c0b3322c64de561487b128ff21bfb53955))

## v0.133.0 (2024-07-15)

### Bug fixes

* fix: remove unused inline ([`f1ff735`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f1ff7354bcaf46258d7a17e36896cde1b5d1abc0))

### Features

* feat: model `related_name` and factory improvements, Bump Versions ([`a8ac195`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a8ac19523af1cb1e56b72a7ae5fcc2571ec16e98))

## v0.132.0 (2024-07-06)

### Bug fixes

* fix: test fix ([`e4fed90`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e4fed901626f2c6ce439838d4e386af5d1b86c80))

* fix: test fix ([`ee1d76b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ee1d76b29d83f0be1c099242fc50fd771f52f9dc))

### Chores

* chore: format files for linting ([`4cf0b88`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4cf0b88b56608ee93dee1169f077da00f993cf55))

### Features

* feat: Use factory boy for seeding and testing, logging improvements, `README.md` updated, Bump Versions ([`c445853`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c44585370f8623dc326025098a357bb895ff6099))

## v0.131.0 (2024-06-17)

### Features

* feat: Bump versions ([`863ba39`](https://github.com/vasilistotskas/grooveshop-django-api/commit/863ba3935d9c72f0a20f931e5ddf8ecefe50ae08))

## v0.130.0 (2024-06-05)

### Features

* feat: Bump versions ([`e0d728e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e0d728e1484b6ac4bb13db995a239143b6489e56))

## v0.129.1 (2024-06-04)

### Bug fixes

* fix: Remove useless ordering option ([`a2d2d60`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a2d2d6035ccff8d3f0ee9a07ff838b2c5db61bc4))

### Chores

* chore: `ACCOUNT_SIGNUP_FORM_HONEYPOT_FIELD = "email_confirm"` added ([`75a7547`](https://github.com/vasilistotskas/grooveshop-django-api/commit/75a75478cdaabc9b60e8a4079d056805385c126a))

## v0.129.0 (2024-06-04)

### Features

* feat: new model field `ImageAndSvgField` and usage in blog category model ([`7242345`](https://github.com/vasilistotskas/grooveshop-django-api/commit/72423458559f2fa893f06fbc49d4e75322a36336))

## v0.128.2 (2024-06-01)

### Bug fixes

* fix: wrong param in user signals remove ([`a40f173`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a40f1736a4c8e089cd53938263a296d4bf6e6c41))

## v0.128.1 (2024-06-01)

### Bug fixes

* fix: wrong param in user signals remove ([`4ecb355`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4ecb35592889869dc4c4518319ca8438dd947337))

## v0.128.0 (2024-06-01)

### Bug fixes

* fix: remove useless tests ([`132db14`](https://github.com/vasilistotskas/grooveshop-django-api/commit/132db143191cb4e9f4b12a8f4787f2023931f009))

* fix: Update `poetry.lock` * ([`fa63b40`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fa63b403f4f8119886e36e29775f088f60c37944))

* fix: Update `poetry.lock` and `README.md` ([`746a2ed`](https://github.com/vasilistotskas/grooveshop-django-api/commit/746a2ed40c18df75915bba04fc35fdc1d9dda13f))

### Features

* feat: Bump Versions and remove useless tests ([`98ec380`](https://github.com/vasilistotskas/grooveshop-django-api/commit/98ec3808fa9949880d132a968262f32e66a07b37))

* feat: Bump versions, improve settings for all auth, implement SocialAccountAdapter and user all auth signals. ([`b192235`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b1922354a71e004b228ff112cfa3ad02c589bd80))

* feat: Bump versions ([`f421809`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f42180964e91854626fff40b8c0fbb424db3e000))

* feat: add "django.contrib.humanize" and app improvements ([`691d196`](https://github.com/vasilistotskas/grooveshop-django-api/commit/691d196ba495559c678cc6d7c8c486d223dc67b5))

* feat: remove `dj-rest-auth` and `django-otp` to use `django-allauth` new headless api ([`e51ccf2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e51ccf2b084a039b980b9046d871b3f5d9226ae2))

### Unknown

* Update README.md ([`03894b0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/03894b0fe1074cd81d94fa5637536725d5e36ce4))

## v0.127.0 (2024-05-25)

### Features

* feat: READEME.md ([`30e5403`](https://github.com/vasilistotskas/grooveshop-django-api/commit/30e5403f0dcd11ca00cd9773c80e705e1b7b60ee))

## v0.126.0 (2024-05-24)

### Bug fixes

* fix: Update poetry.lock ([`9ed3378`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9ed3378413000ad0980addaaf8fa2675d6c656f5))

### Features

* feat: Bump Version ([`558c682`](https://github.com/vasilistotskas/grooveshop-django-api/commit/558c6826bcbc4dcb55cfb12f1be4c609c5fd94f9))

## v0.125.0 (2024-05-19)

### Features

* feat: Contact form ([`f8ba7e3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f8ba7e3cd2704fd7d6fc160c4cfcd3e46ecf94b1))

## v0.124.2 (2024-05-18)

### Bug fixes

* fix: order API `retrieve_by_uuid` method ([`5445ea5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5445ea51afc8e866fadfd12f6675c4e23ddd0298))

## v0.124.1 (2024-05-18)

### Bug fixes

* fix: add ordering_fields at blog category posts ([`92c20ce`](https://github.com/vasilistotskas/grooveshop-django-api/commit/92c20ce2e7cc55babceb886013c4cae7d5159055))

## v0.124.0 (2024-05-18)

### Features

* feat: Bump Versions ([`5c9e163`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5c9e16387af89abf3ca3487d9eefdb4b1781a951))

## v0.123.0 (2024-05-16)

### Bug fixes

* fix: tests include username ([`69e7bd1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/69e7bd13dc0686b52be2acb56ddefb5b707b208b))

### Features

* feat: Be able to log in via username ([`14c41bc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/14c41bca9b2e81dbde058a7767cd42752c743b9d))

## v0.122.0 (2024-05-14)

### Features

* feat: Add MFA_TOTP_ISSUER setting ([`620905b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/620905bb8c49cf570ddfa037bdb361f7538be259))

* feat: Bump Versions ([`d49c1c8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d49c1c824c41ede911b91303bdb328a7a54d5f30))

## v0.121.0 (2024-05-13)

### Bug fixes

* fix: update poetry ([`148f94c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/148f94cde41f2000ea5c356a5801b5c8db9dc011))

### Features

* feat: Remove settings from core and use new library `django-extra-settings` ([`685c99a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/685c99a26129976f5ab012ff48ec8b7147f445e7))

## v0.120.0 (2024-05-13)

### Features

* feat: Bump Versions ([`77d93ea`](https://github.com/vasilistotskas/grooveshop-django-api/commit/77d93ea43e388d3b5c5d9c3b99821a0f72308c49))

## v0.119.0 (2024-05-07)

### Features

* feat: Add username change API endpoint ([`0e3ee78`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0e3ee782903b32b6b4f0a5c4457ef43971529508))

## v0.118.0 (2024-05-07)

### Features

* feat: Bump Versions ([`75d024a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/75d024aa588c4d34b15f03eea556a7235616f882))

## v0.117.0 (2024-05-07)

### Features

* feat: Bump Versions ([`2522e11`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2522e11d66f212536c46001b163f782c93a1a295))

## v0.116.1 (2024-05-06)

### Bug fixes

* fix: update `Notification` app urls ([`871294c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/871294c96f167dd4ef6ff6f6c548fa54566ec03b))

## v0.116.0 (2024-05-06)

### Features

* feat: Bump Versions ([`bfcc249`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bfcc249195d9c51c7691c72cd73a3c1eeaabe539))

## v0.115.1 (2024-04-27)

### Bug fixes

* fix: correct mfa test ([`a447c5d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a447c5d0933a86527f3338e491e995cc6cf68be0))

## v0.115.0 (2024-04-27)

### Bug fixes

* fix: pass the celery test for updated method ([`45f4ec2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/45f4ec22b811e3f9fbd71dd5de66e08ff827fe93))

### Features

* feat: Dev env improvements, core urls.py cleanup and Bump Versions ([`6532f5e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6532f5e0eb56195efe5c6724b552afdbb87214fe))

## v0.114.0 (2024-04-26)

### Features

* feat: Remove `app` module, move `settings.py` in root ([`c773a3b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c773a3b21446a0ad0135fb89a59c31ecc30b292c))

## v0.113.0 (2024-04-26)

### Features

* feat: Add username for user account ([`456db5a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/456db5a32c7c8571e2e4a42a0ed73e43017ece2e))

## v0.112.2 (2024-04-25)

### Bug fixes

* fix: docker dev env pgadmin initial server works ([`82c0605`](https://github.com/vasilistotskas/grooveshop-django-api/commit/82c0605c8a17e9b0681122298a9cee52af6fe87b))

## v0.112.1 (2024-04-25)

### Bug fixes

* fix: minor admin improvements and set `INDEX_MAXIMUM_EXPR_COUNT ` to 8k ([`d1c702c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d1c702cffd48f31fac64735e8a571d1bcdff36d3))

## v0.112.0 (2024-04-25)

### Features

* feat: Add caching in search results API ([`0fe6179`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0fe61795e7fffa1da8226e4383d5c65fba3c5f11))

## v0.111.0 (2024-04-25)

### Features

* feat: Improve text preparing for search fields ([`7db90cd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7db90cd8f13fe561cf1ed4d8e6d09cc2fe30f394))

## v0.110.3 (2024-04-25)

### Bug fixes

* fix(prepare_translation_search_vector_value): remove existing html from fields ([`829862d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/829862d1baed889a7849331eadd330ae6fb334df))

## v0.110.2 (2024-04-25)

### Bug fixes

* fix: Add output_field in `FlatConcatSearchVector` ([`d0b204a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d0b204a249cd1d7a5aa3fdd785f0aa7838dd86cb))

## v0.110.1 (2024-04-25)

### Bug fixes

* fix: `prepare_translation_search_document` and `remove_html_tags` fix ([`1f61bb9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1f61bb96e868d655c2faa00d1aeaca231b3124a5))

## v0.110.0 (2024-04-24)

### Features

* feat: Search vectors and docs add to blog post and improve whole search concept, Bump Versions ([`de952ad`](https://github.com/vasilistotskas/grooveshop-django-api/commit/de952ad31b029f090ea7c8414ad7fbcfb1a40e90))

## v0.109.0 (2024-04-23)

### Features

* feat: Bump Versions and add throttling in some API endpoints ([`fc49b91`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fc49b91773fc7356e6b8014ace09befd11c0e856))

## v0.108.0 (2024-04-22)

### Bug fixes

* fix: Remove useless model verbose name tests ([`7cae556`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7cae55669481245413339039c9404a5bb7d0c55d))

### Features

* feat: Default lang `el` by default ([`e492900`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e4929006cf0ef7626fc27ec27813c5ebd5c6153b))

## v0.107.0 (2024-04-21)

### Features

* feat: Add cache disable from env options ([`b26b7c6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b26b7c6397eb566086cc67edbba957f6438da3e0))

## v0.106.1 (2024-04-20)

### Bug fixes

* fix: title ([`d30fa30`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d30fa3066a8d8ee7ea786814c2c0899a06e5b54e))

## v0.106.0 (2024-04-20)

### Features

* feat: Task improvements new API endpoint `liked_blog_posts` and favicons replaced ([`7738aa1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7738aa105bef036439a2343f8f77134c17b55175))

## v0.105.0 (2024-04-18)

### Features

* feat: Bump Versions ([`578e178`](https://github.com/vasilistotskas/grooveshop-django-api/commit/578e178afcf0627e61b824d7b53c91d55f86379e))

## v0.104.1 (2024-04-17)

### Bug fixes

* fix: Remove useless validators from models ([`bb36840`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bb3684017eeea109c96f1fc87c480c611d26e055))

## v0.104.0 (2024-04-17)

### Features

* feat: Bump Versions ([`19e3f86`](https://github.com/vasilistotskas/grooveshop-django-api/commit/19e3f86c4ff1b897c9f944975be416247c96e21c))

* feat: Bump Versions ([`ae033d2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ae033d2789cd2e262d148e39617ebdbcfbf40a24))

## v0.103.0 (2024-04-16)

### Bug fixes

* fix: remove useless stuff and bump `dj-rest-auth` version ([`26639a1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/26639a10e59098c57b1ff9178188f06b1ff09aff))

### Features

* feat: Hardcoded values from env ([`39dfbb2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/39dfbb249a46f68ea14a3d25393df5ca498e03d2))

## v0.102.1 (2024-04-15)

### Bug fixes

* fix: hide some urls in production ([`ed2628a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ed2628a9f3048c3a3eadc6fda7f6a4e2037fd98c))

## v0.102.0 (2024-04-15)

### Features

* feat: Bump Versions ([`48adc32`](https://github.com/vasilistotskas/grooveshop-django-api/commit/48adc32c2628a6fd74047892e6294587278bf4a1))

## v0.101.0 (2024-04-13)

### Bug fixes

* fix: Update test for lib update ([`b787ca1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b787ca1e276218075f368b2b79b48a11d2cbceca))

### Features

* feat: New API endpoints and improvements ([`a3deae2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a3deae2d22e7cc94a9040794c787551c92c2c81c))

## v0.100.0 (2024-04-10)

### Features

* feat: Bump Versions ([`ebf9cb9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ebf9cb98c2cdbfc6fc1d53ae75d1d56a507e6c0e))

## v0.99.1 (2024-04-10)

### Bug fixes

* fix: update hardcoded configs ([`340bb2b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/340bb2bd35a961f4f597bb102ffc33e2d03b5726))

## v0.99.0 (2024-04-08)

### Features

* feat: Bump Versions ([`d788413`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d788413566e319205111d164b88eec85bf255a75))

## v0.98.0 (2024-04-07)

### Bug fixes

* fix: remove echo from ci.yml ([`e87f90d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e87f90d10ae06751b3133385ae3b6410835170ee))

* fix: Use pipx in ci.yml ([`0776184`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0776184c8c0c07e3a0531d0c682eef5b64994396))

* fix: Bump poetry version ([`2cb1741`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2cb17416c67c5a11f924b0080c0d438cf32a31ef))

### Features

* feat: Update schema.yml, make locales and fix `Echo` in ci.yml ([`801d787`](https://github.com/vasilistotskas/grooveshop-django-api/commit/801d787e49287b6d768b8af2315e807390feb1f6))

* feat: Bump github workflow ci.yml versions and poetry version bump ([`2d6ce52`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2d6ce5216902cdde8246fab21c3ab11c73fffc69))

## v0.97.0 (2024-04-06)

### Features

* feat: serializers expand `BaseExpandSerializer` and filter language ([`e361373`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e36137368b4bee9c6531ea52b0067c6b8ce2bf85))

## v0.96.0 (2024-04-04)

### Features

* feat: Blog post comments / replies and likes ([`7ed0be3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7ed0be33fd1fba35442c5eeaeeeede920b59b79f))

## v0.95.0 (2024-03-31)

### Features

* feat: Index fields to improve performance ([`3eb3ea1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3eb3ea1c36eb59e1b337ef3e6ad12f3a6624a096))

* feat: blog category posts API endpoint and code cleanup ([`c28727e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c28727e0f60ecc5a809cd1a6b5430fe495061f52))

## v0.94.1 (2024-03-29)

### Bug fixes

* fix: Product API view fix ([`fac6862`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fac6862e3753a847ed46b49cbf5034927caa8dd3))

## v0.94.0 (2024-03-29)

### Features

* feat: Paginators remove, now have option to ask for a pagination type from query string, useless extented api view methods removed ([`49bf512`](https://github.com/vasilistotskas/grooveshop-django-api/commit/49bf512825d8d1066a11cd7c6a4669961f84e38a))

## v0.93.1 (2024-03-27)

### Bug fixes

* fix: tests fixed ([`850b0ff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/850b0ffdb296993c861f17757f47bb78bd5cc658))

* fix: Fix populate commands, search improve and fix model image_tag methods ([`b2ebfa7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b2ebfa70536420efc974ae59efe2b26e909b9afc))

## v0.93.0 (2024-03-25)

### Features

* feat: search improve and Bump Versions ([`8a7e2ad`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8a7e2adf7105e10dd6b87e2256d3204484b681d9))

## v0.92.0 (2024-03-25)

### Features

* feat: search improve ([`43976f0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/43976f0650aeee9f94497c21997c460c474cc687))

## v0.91.0 (2024-03-24)

### Bug fixes

* fix: update search test ([`21ad6f0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/21ad6f0931275fbbf6dd9f6eb5a97aa7c0a9c4f7))

### Features

* feat: Bump Versions and search improve ([`85626f7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/85626f7103362d709ffcbfb7bb45ab7bc3c52027))

## v0.90.0 (2024-03-23)

### Features

* feat: Task updates and Bump Versions ([`e2387f5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e2387f5d6c87c0a5ebb5ef9699c4c7e7a6c950d5))

## v0.89.0 (2024-03-22)

### Features

* feat: Bump Versions ([`1058369`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1058369bd91b671a9e719fb75cff65dc6b2a9c89))

## v0.88.0 (2024-03-21)

### Features

* feat: Bump Versions ([`01f4fd0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/01f4fd03dbf02a514d6277bd5938400ccea63a4a))

## v0.87.0 (2024-03-19)

### Features

* feat: Bump Versions and remove timezone middleware ([`d44c9de`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d44c9defb5b03f48607a02f0a82aad214d4fc31c))

## v0.86.0 (2024-03-17)

### Features

* feat: Cache some api views ([`81daec1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/81daec1ce5807bdc051357cbb1fe19fcc941e26b))

### Unknown

* fix:(registration.py): Not verified email case ([`76960cf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/76960cf22b1403454a126a3c36a0b0664fec9a4f))

## v0.85.1 (2024-03-16)

### Bug fixes

* fix(Dockerfile): remove ` postgresql-dev` lib ([`ce7c3e8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ce7c3e8454106c4a9b757348e9780f851eda5bf9))

## v0.85.0 (2024-03-16)

### Bug fixes

* fix(test_view_search): fix ([`cbb7ef7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cbb7ef7bad71b28aea3ce969f1e5d995f2b76880))

### Features

* feat: New celery tasks ([`f8df788`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f8df788e43b2cc92c9cee357df8ad04d0e9cbb19))

## v0.84.0 (2024-03-11)

### Bug fixes

* fix: decode query for search after validation ([`b783bbb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b783bbb41b808517bc90c2bfbea795c5250279fb))

### Features

* feat: search improvements ([`f98d033`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f98d03391342a1fcde1a016680d7b56f50b07387))

## v0.83.0 (2024-03-09)

### Features

* feat: Search and Celery Task improvements ([`13f2fb3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/13f2fb35df9dfff0ab285896054f9cff7059d10e))

## v0.82.0 (2024-03-09)

### Bug fixes

* fix: `test_models` Database access not allowed fix ([`1db493b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1db493b1a2a15a06b83595687451a4f52860f32b))

### Features

* feat: Measurement implemented and Bump Versions ([`e1c7d79`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e1c7d79919c25b09ff0fc36d75660f9cb8aa40a3))

## v0.81.0 (2024-03-08)

### Features

* feat: New api endpoint to get product images ([`aa87bcf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/aa87bcf78dd8b578fdd0e8414a317f002eb83d49))

## v0.80.0 (2024-03-08)

### Features

* feat: New api endpoint to get product review and Bump Versions ([`78fc042`](https://github.com/vasilistotskas/grooveshop-django-api/commit/78fc042953ce86a5ec321c89d10f6d07b8f0b3ad))

## v0.79.0 (2024-03-07)

### Features

* feat: `blog_post_comments`, `blog_liked_posts` and `blog_liked_comments` added in user details API view, get blog article comments api endpoint and method names correction ([`7feb1d9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7feb1d90bfa69b9f33eefcd9c8d139e33c6fdada))

## v0.78.0 (2024-03-07)

### Features

* feat(Dockerfile): Bump python version to 3.12.2 ([`80d1a5f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/80d1a5f023c9818454a113f9b76a46d701276de2))

## v0.77.0 (2024-03-05)

### Bug fixes

* fix: remove comments from tests package ([`2da561a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2da561a096f4352a9eae1f80baa2b91c992199ce))

### Features

* feat: `clear_blacklisted_tokens_task` task, `user_to_product_review` rename to `user_product_review` and config package simplify ([`906f845`](https://github.com/vasilistotskas/grooveshop-django-api/commit/906f84537033ebc60e2d118efdafcc9682f4fac6))

## v0.76.0 (2024-03-03)

### Features

* feat: Bump Versions

- Django 5.0 and django-celery-beat 2.6.0 ([`069b838`](https://github.com/vasilistotskas/grooveshop-django-api/commit/069b8384376da556fea256f2adcad15921a4dc90))

## v0.75.0 (2024-03-01)

### Features

* feat: introduce `rest_framework_simplejwt.token_blacklist` ([`7653430`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7653430c95beced7d8875f35003b0ee87cb0715a))

## v0.74.0 (2024-03-01)

### Features

* feat: `user_had_reviewed` method change to `user_to_product_review` and change user addresses model ordering Meta field ([`a5ebf0e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a5ebf0eb77ed330f1d66a7cac2ad0e91b757de79))

## v0.73.0 (2024-02-26)

### Features

* feat: Bump Versions ([`12dfecd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/12dfecde77567f772388027f1026486e924dbf44))

## v0.72.0 (2024-02-24)

### Features

* feat: Auth user account API improved ([`faf8712`](https://github.com/vasilistotskas/grooveshop-django-api/commit/faf87122d228e72fe3e503da3d02bb2b46beff1a))

## v0.71.0 (2024-02-17)

### Features

* feat: Bump Versions ([`9600e92`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9600e92491efdfda93d192fd5f5869329841bba9))

## v0.70.0 (2024-02-16)

### Features

* feat: API serializers with `get_expand_fields` imports using `importlib`, BaseExpandSerializer logic improvement, BlogComment model use `MPTTModel`. ([`62d9591`](https://github.com/vasilistotskas/grooveshop-django-api/commit/62d9591600a1fe22dfe41e21ded1c96ecfc00a6a))

## v0.69.0 (2024-02-14)

### Features

* feat(upload_image): save img to S3 storage on production ([`d7a5207`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d7a520703c2ad535e3a6dacb0aeabac6e63a68ed))

## v0.68.0 (2024-02-14)

### Bug fixes

* fix: `test_model_product_category` absolute url test ([`af99e61`](https://github.com/vasilistotskas/grooveshop-django-api/commit/af99e611ae7a36751b4c20d0d104b87f8a7bc8c3))

* fix: `test_model_product_category` absolute url tests ([`664dcca`](https://github.com/vasilistotskas/grooveshop-django-api/commit/664dcca700a589f04d740554a8895740382f6a11))

### Features

* feat: `ProductFilter` method `filter_category` include descendants , `ProductCategory` absolute url fix ([`bb90d4f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bb90d4f4e1558ab3ab273352d6009621a9ca484c))

## v0.67.0 (2024-02-13)

### Features

* feat: Django tinymce be able to upload image in `HTMLField` tinymce ([`e2ed8d0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e2ed8d07ef86210e63f5f37d8f0e0385719db616))

## v0.66.0 (2024-02-12)

### Features

* feat: Settings application and Bump Versions

- New Settings application to set single key value setting for the application.
- Tests for new settings app implemented.
- Bump Versions ([`4501e8d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4501e8d7bd4d49afbb8ada133ad5b13be5ce57ab))

## v0.65.0 (2024-02-09)

### Features

* feat: Bump Versions ([`2bb5ecb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2bb5ecbca0d7bed2195151c82325d5182ffadb54))

## v0.64.0 (2024-02-06)

### Bug fixes

* fix: run `poetry lock` ([`d459749`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d459749bcb6eab543a7df02a7500fdb2e78c44ab))

### Features

* feat: Bump Versions and new features ([`16f5709`](https://github.com/vasilistotskas/grooveshop-django-api/commit/16f5709ca55bff44fe9fa151f877974b58fc66c6))

## v0.63.0 (2024-01-23)

### Features

* feat: Bump Versions ([`695da1e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/695da1e14899b9f389aaa05bcc145dc8dc992fc6))

## v0.62.0 (2024-01-16)

### Features

* feat: Bump Versions ([`0befa3a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0befa3aa30721dfeb0f50914324d338d1d3d7a82))

## v0.61.0 (2024-01-08)

### Features

* feat: Bump Versions ([`54c8138`](https://github.com/vasilistotskas/grooveshop-django-api/commit/54c8138b22f9af93cfc4fb081a945f9a70e07adc))

## v0.60.0 (2024-01-03)

### Features

* feat: Bump Versions ([`b6623dc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b6623dc58b21fb8453347fd1b88fa40c9f6c2130))

## v0.59.1 (2023-12-29)

### Bug fixes

* fix: `test_storage` fix to pass previous commit ([`c0cea9e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c0cea9e85bd965264dfdc4b6ded4a8d1f9f90ab2))

* fix(storage.py): change staticfiles class ([`152974e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/152974eb385c1e932d0e54204c23d2c31f1f8128))

## v0.59.0 (2023-12-28)

### Features

* feat: Bump Versions and add `absolute_url` in blog post ([`50bf02a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/50bf02a69e01c5e7c25ab98bfc6bbc2ee62f07c1))

## v0.58.0 (2023-12-24)

### Features

* feat: improve `BaseExpandSerializer` and versions bump ([`7b4f13e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7b4f13e9e6f465115af677284707a7b408496222))

## v0.57.1 (2023-12-23)

### Bug fixes

* fix(OrderCreateUpdateSerializer): validate and create methods ([`b11aa31`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b11aa3170476e0d512769476cf8861a5a7926efb))

## v0.57.0 (2023-12-19)

### Features

* feat: Bump Versions ([`dd8cbcd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dd8cbcde037d792291a3f19862a8376037a841b1))

## v0.56.0 (2023-12-15)

### Features

* feat: Add `multidict` ([`2dcd119`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2dcd11913f42fb9d00d0e5f68b763adeea3c21bc))

## v0.55.5 (2023-12-15)

### Bug fixes

* fix(Dockerfile): Add `build-essential` ([`a1fb346`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a1fb3466aacdaeb3d951aed0f002cb9142287e2d))

## v0.55.4 (2023-12-15)

### Bug fixes

* fix(Dockerfile): Remove ` libmusl-dev` ([`f419494`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f4194944f7da88270c85cb3f9297e41af577c038))

## v0.55.3 (2023-12-15)

### Bug fixes

* fix(Dockerfile): use `apt-get` ([`6a82c76`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6a82c76c51d1d0a64bb179e27d0454c18bb65d11))

## v0.55.2 (2023-12-15)

### Bug fixes

* fix(Dockerfile): `groupadd` fix ([`f0d52f4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f0d52f48776211917a84df780f9cb5045720dfaa))

## v0.55.1 (2023-12-15)

### Bug fixes

* fix(Dockerfile): use apt-get ([`2526e4b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2526e4bed4578af5e8aa0f408105e11d8210cdf6))

## v0.55.0 (2023-12-15)

### Bug fixes

* fix(Dockerfile): Use `python:3.12.1-slim` ([`4dd97ac`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4dd97accbe0937c11a7dd9d7b52e3b4b3dfa423e))

### Features

* feat: Bump Versions ([`90f3c97`](https://github.com/vasilistotskas/grooveshop-django-api/commit/90f3c97b6fbc3324331cb5e92332a0dbad754ba9))

## v0.54.0 (2023-12-12)

### Features

* feat: Bump Versions ([`b1e0351`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b1e03519819423f968fc2c02eb5e3f2636e68218))

## v0.53.0 (2023-12-08)

### Features

* feat: Django 5 ([`ce3827a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ce3827ac91e491750f0a2a99857ab477576888e1))

## v0.52.1 (2023-12-08)

### Bug fixes

* fix(ci.yml): Remove hardcoded omit and pass the `.coveragerc` file ([`a67886b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a67886b4fd8db15f8f5c700c4f6bfd64817b2afd))

## v0.52.0 (2023-12-08)

### Features

* feat: Bump Versions ([`262ea57`](https://github.com/vasilistotskas/grooveshop-django-api/commit/262ea5726cda6d4d636dd51bd19408d4b0df318b))

## v0.51.0 (2023-12-07)

### Features

* feat: New tests and some config fixes ([`74fb54f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/74fb54f0d79e4b68043e88078118515c9cecb624))

## v0.50.0 (2023-12-06)

### Bug fixes

* fix(config/logging.py): Create the directory if it does not exist ([`4c486bf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4c486bfbfe82de665e3571aa4f7eedd9ddb02cf7))

### Chores

* chore: Delete logs/celery.log ([`edab86e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/edab86e014733dc5f1b495d1678732babf310628))

### Features

* feat: Logger improve and task to remove old logs ([`b7db338`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b7db338c052da4700178e8c9514b0b96195fcb9e))

## v0.49.1 (2023-12-06)

### Bug fixes

* fix: Reduce product pagination `default_limit` ([`04b1e9e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/04b1e9e2cb883edbd93d56aeba853853fba1599b))

## v0.49.0 (2023-12-05)

### Features

* feat: reduce `MoneyField` max_digits and decimal_places and tests fix ([`2ef6ef4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2ef6ef41bf61828be5ace62e547097940efec50b))

* feat: Bump Versions and remove `SessionAuthentication` from `DEFAULT_AUTHENTICATION_CLASSES` config. ([`5a579a0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5a579a0db717381c54b8ebaa9e3ed5bca2a544b5))

## v0.48.0 (2023-12-03)

### Features

* feat: `GITHUB_WORKFLOW` env `SYSTEM_ENV ` var rename to `ci`, remove session endpoints and versions bump ([`fb24b1b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fb24b1bcc0b16d92ec33e680d373c2e61429ae7d))

## v0.47.6 (2023-11-24)

### Bug fixes

* fix: tinyMCE url fix and new config env vars. ([`0415f6b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0415f6b2f4f81d260c5d47f0511734e489f0e94a))

## v0.47.5 (2023-11-24)

### Bug fixes

* fix: tinyMCE url fix. ([`1c94295`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1c9429588266d5b74df689a25e0622baef348628))

## v0.47.4 (2023-11-24)

### Bug fixes

* fix: set TINYMCE_COMPRESSOR to false ([`79fdfa3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/79fdfa3231c3cc6a93d38f72cbf19f6d8187e437))

## v0.47.3 (2023-11-24)

### Bug fixes

* fix: tinyMCE url fix. ([`e30b08a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e30b08ab32fd564a2e10a92a57b3c843a93dc455))

## v0.47.2 (2023-11-24)

### Bug fixes

* fix: tinyMCE url fix. ([`fcf0b43`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fcf0b434515141adbd4a5bb0a2d173be261edece))

## v0.47.1 (2023-11-24)

### Bug fixes

* fix: Bump tailwind version and add `--minify` flag ([`b8d4f49`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b8d4f49a0ba39cd9911c25cbac32775a645e5397))

## v0.47.0 (2023-11-23)

### Features

* feat: Bump Versions ([`3534020`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3534020a1933e058fd3fee85aaa3dc439cc6fe69))

## v0.46.4 (2023-11-21)

### Bug fixes

* fix: increase DEFAULT_THROTTLE_RATES for production ([`e7ad5fd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e7ad5fd70eea439b5771785877354407e146e4ed))

## v0.46.3 (2023-11-21)

### Bug fixes

* fix: debug toolbar check ([`6585a76`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6585a761dfeff6fc09e4c0442f2d948aaeae587b))

## v0.46.2 (2023-11-21)

### Bug fixes

* fix: ALLOWED_HOSTS fix ([`8fb3a7b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8fb3a7b51d4eb2c579a80d6cf8c6635364c3bcc0))

## v0.46.1 (2023-11-21)

### Bug fixes

* fix: `APPEND_SLASH` default to false ([`bcfb5e8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bcfb5e81284232aba1c1963873a0651f50aa201b))

## v0.46.0 (2023-11-21)

### Features

* feat: Remove trailing slash from all api end point urls ([`22d72de`](https://github.com/vasilistotskas/grooveshop-django-api/commit/22d72de95c353249633e300d4da6eceac98f99dd))

## v0.45.5 (2023-11-21)

### Bug fixes

* fix: add`ALLOWED_HOSTS` ips ([`00611bb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/00611bbb053cc3fbb9d9371da2cae12379154a17))

## v0.45.4 (2023-11-21)

### Bug fixes

* fix: fix `ALLOWED_HOSTS` missing coma ([`2339a29`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2339a29d786bd84e58ad11e880d2eddf36be5a94))

## v0.45.3 (2023-11-21)

### Bug fixes

* fix: update config base.py ([`7550aac`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7550aacfe9b3490feb3484c449dfbab30dcca3b6))

## v0.45.2 (2023-11-21)

### Bug fixes

* fix: celery import fix, ALLOWED_HOSTS fix. ([`91d6216`](https://github.com/vasilistotskas/grooveshop-django-api/commit/91d62169da19a6808d72e7c23e392f51b54a259c))

## v0.45.1 (2023-11-19)

### Bug fixes

* fix: config fix ([`c6cb058`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c6cb058b1e88de001d5bd204e3a7d9ee6db70d85))

## v0.45.0 (2023-11-19)

### Bug fixes

* fix: poetry update ([`629a97b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/629a97bf828138caa641b80d1c42c97f001bd156))

### Features

* feat: versions bump and update bucket ([`dea38a3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dea38a3eab011b78e5b1de5a0ca4c94e3a4476d5))

## v0.44.3 (2023-11-17)

### Bug fixes

* fix: config security fixes ([`fe64d98`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fe64d9814f90cc86ea1a52656d689512d4e60a31))

## v0.44.2 (2023-11-17)

### Bug fixes

* fix: remove `SessionTraceMiddleware` ([`476e9a3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/476e9a3c3c60269f80a37dd507bcd7d218ab7b04))

## v0.44.1 (2023-11-17)

### Bug fixes

* fix: add `websockets` lib ([`8f7064c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8f7064c42e5fa97b9a990679bc4071f64416905a))

### Chores

* chore: lint ([`456b35a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/456b35afd4c92fd263fa1166f355f9ae27accdab))

## v0.44.0 (2023-11-17)

### Features

* feat: clear all cache task and command, `ATOMIC_REQUESTS` set True, and run app with uvicorn. ([`becd1b5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/becd1b58630d58d586488dbea8e244e43aada4b8))

## v0.43.2 (2023-11-16)

### Bug fixes

* fix: move settings to db ([`bd3f6a8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bd3f6a89c603840d7d5c6fe6b68e4cfec00eb5ea))

## v0.43.1 (2023-11-16)

### Bug fixes

* fix: default debug `True` revert for now ([`e4f0953`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e4f0953cba2605c4b4beeb207142dde4e590e5c5))

* fix: enable `ATOMIC_REQUESTS` and added `CONN_MAX_AGE` to 60 sec ([`da825ef`](https://github.com/vasilistotskas/grooveshop-django-api/commit/da825ef0309e210c573ed0b2eb904b11a0e6eb6b))

* fix: debug default false ([`61a9aad`](https://github.com/vasilistotskas/grooveshop-django-api/commit/61a9aaddc66b08347519a1cccddd785dc3506c8e))

## v0.43.0 (2023-11-16)

### Bug fixes

* fix: remove useless test ([`7ffd294`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7ffd294d8002182e8f83ca3fe9e97e26c1d3c95d))

### Features

* feat: Versions bump and new tests ([`c075648`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c0756486d6cdba902045451582d10c0e86cd80fb))

## v0.42.0 (2023-11-14)

### Features

* feat: Bump Versions ([`94f7fa3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/94f7fa35e0d3cf7f70d162601d28516d028cb6f4))

## v0.41.0 (2023-11-13)

### Features

* feat: Versions bump ([`26b2332`](https://github.com/vasilistotskas/grooveshop-django-api/commit/26b23323e4223645229d33ca7f5df0445640be2c))

## v0.40.5 (2023-11-12)

### Bug fixes

* fix: `cors.py` config origins ([`4320cda`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4320cda1ba45674b2d586562f421971852bcbc9f))

* fix: `csrf.py` config origins ([`98a1751`](https://github.com/vasilistotskas/grooveshop-django-api/commit/98a1751995818eb9489f3697cb4cb2885eac4bf2))

* fix: update config ([`2cb190f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2cb190fb17b4316574868fb1a60fe3aab0d562ba))

### Chores

* chore: Delete .idea directory ([`a29459b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a29459b5a0f42f87df6b09c6431e6b23cea7753b))

## v0.40.4 (2023-11-09)

### Bug fixes

* fix: redis url remove from env, versions bump, add new `ALLOWED_HOSTS`. ([`01aa977`](https://github.com/vasilistotskas/grooveshop-django-api/commit/01aa977284dea55cac8f9949f67bb0ece49dc0e3))

## v0.40.3 (2023-11-08)

### Bug fixes

* fix: config improve and remove useless stuf. ([`dd26ba5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dd26ba56a5e408639bbe340f0cb0003e04cb3c2b))

## v0.40.2 (2023-11-05)

### Bug fixes

* fix: session middleware `update_session` wrap with try catch and versions bump. ([`21d798e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/21d798eab75db750906debf59a6b099ed4f29e39))

## v0.40.1 (2023-11-04)

### Bug fixes

* fix: `.env.example` revert, `.gitignore` fix. ([`2a5200a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2a5200a763b0c46812130087999a578b0e26fd5b))

* fix(.gitignore): Only include `.env.example` ([`f80ac08`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f80ac0871f9418d9f907a75717dd0cc12d79c647))

### Chores

* chore: remove env files. ([`fdccd2e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fdccd2ee1cc5cf7e45d4826fb78620ad90effffe))

## v0.40.0 (2023-11-03)

### Features

* feat(config): Added `NGINX_BASE_URL` . ([`cde1bcc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cde1bccc15ad5e814c02ac7f1f42a54629e8be85))

## v0.39.9 (2023-11-03)

### Bug fixes

* fix: Include `qrcode` library. ([`53750e0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/53750e03ad24aeb112a6f629954de73450505e52))

## v0.39.8 (2023-11-03)

### Bug fixes

* fix: Include `httptools` library. ([`addff50`](https://github.com/vasilistotskas/grooveshop-django-api/commit/addff5023825e3ee00b187954f0ddc7f3b9b5e44))

## v0.39.7 (2023-11-03)

### Bug fixes

* fix: Include `uvloop` library. ([`45290ff`](https://github.com/vasilistotskas/grooveshop-django-api/commit/45290ffa6b0e81d5125fcd82b63581a9af559a8b))

## v0.39.6 (2023-11-03)

### Bug fixes

* fix: Added `phonenumbers` package. ([`e6eeb8e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e6eeb8eee0c682efaf5039c45aaa931cbf00e565))

## v0.39.5 (2023-11-03)

### Bug fixes

* fix(Dockerfile): trying to work with psycopg ([`b4c353d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b4c353d314e3743242762806f35d7523f5512de6))

## v0.39.4 (2023-11-03)

### Bug fixes

* fix(Dockerfile): fixed ([`45391b6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/45391b6081c1515af9459b9a08b697f957220cb3))

## v0.39.3 (2023-11-03)

### Bug fixes

* fix(Dockerfile): Fixed. ([`f4b1d3f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f4b1d3f48a9ae36b0a55f787b9406c16984fa979))

## v0.39.2 (2023-11-03)

### Bug fixes

* fix: Dockerfile fix, djang0 fix to django ([`dedb407`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dedb40732aaf5e49180646d8f106a23f480685cf))

## v0.39.1 (2023-11-03)

### Bug fixes

* fix(Dockerfile): Add empty logs file. ([`7893a7b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7893a7b19e868108c509cadaf1fa20fdc752a407))

## v0.39.0 (2023-11-03)

### Features

* feat: More at `Description`

ci github actions test using pytest.
- asgi and wsgi refactor and improved.
- versions bump. ([`a761994`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a7619946e1487c6218fe285c1a876f08ff5c8512))

## v0.38.0 (2023-10-31)

### Bug fixes

* fix: update `poetry.lock` ([`98e509a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/98e509a1098758efe30ef1077c8225682fbe687d))

### Features

* feat: Versions bump, `asgi.py` router improved. ([`9090c28`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9090c28ec3babf9a60cd3845b3364d6d16e3fc45))

## v0.37.4 (2023-10-31)

### Bug fixes

* fix: celery settings update, caches.py update, Dockerfile remove lint step. ([`208471a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/208471a2b8fb2fcbb8976db383839db182585576))

## v0.37.3 (2023-10-31)

### Bug fixes

* fix(Dockerfile): fix addgroup ([`3159bb9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3159bb93d89cda65accf60a8aa037ea6ec529a76))

## v0.37.2 (2023-10-31)

### Bug fixes

* fix(Dockerfile): replace apt-get with apk ([`6d53af1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6d53af12374f8521a63b332168cf4ec2179b6dba))

## v0.37.1 (2023-10-31)

### Bug fixes

* fix: docker file fixes for github actions ([`0d500c0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0d500c0bcf4db1dc27481a220d65f0c130929c09))

## v0.37.0 (2023-10-31)

### Features

* feat(docker.yml): add requirements for github actions cache. ([`4cbbc89`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4cbbc898674d26704fd501c6e42b54839629dbe2))

## v0.36.3 (2023-10-31)

### Bug fixes

* fix(docker.yml): Update dockerfile path and add cache. ([`349cc26`](https://github.com/vasilistotskas/grooveshop-django-api/commit/349cc26b2c6b4a9e61af5f333fe0f8f00e9d1da1))

## v0.36.2 (2023-10-30)

## v0.36.1 (2023-10-30)

### Bug fixes

* fix(docker.yml): Update dockerfile path. ([`b856bb1`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b856bb1b771d1d8bcad4594038f3fb17fe852c28))

* fix(cache.py): change redis host in github actions. ([`446e105`](https://github.com/vasilistotskas/grooveshop-django-api/commit/446e1058d0e5ede29c749f92519cfb6a59a930e9))

* fix: `cache.py` fixed. ([`240c3b3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/240c3b3c07efd697dce6065d9eacea8616d8fe66))

* fix: Update staticfiles and mediafiles ([`ba83b89`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ba83b890ec3f76a7fa333cec3f682f33d746404e))

* fix: More at `Description`

- celery improvements , `celery_setup.py` rename to `celery.py`.
- cache and session improvements.
- locales generate, API `schema.yml` generate,. ([`16e0f6d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/16e0f6d43157ed5bdec82553594bd3ada27d8049))

## v0.36.0 (2023-10-28)

### Bug fixes

* fix: ci.yml cleanup ([`50dbaf9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/50dbaf97c5419ef316d377ba01c3153533f7135f))

* fix: Ci release step setup python added. ([`84b7f27`](https://github.com/vasilistotskas/grooveshop-django-api/commit/84b7f2739a3cf239b546be783e52d09df6844c53))

* fix: Update poetry lock ([`fd62504`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fd62504d4cc3482b4da58c7ba5fc84314c0db6c9))

### Chores

* chore: Move docker files under docker folder. ([`f02fc33`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f02fc336998d52c0e4fcf593fe1f245f035eedd6))

### Features

* feat(ci.yml): Add redis service ([`0458d56`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0458d56e9382becf18ad8aeab8c6f82ad375d8cb))

* feat: Preparing for docker setup. ([`6e2f142`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6e2f1426bf0af4b59b5815da282ccc645419a3bc))

## v0.35.0 (2023-10-20)

### Bug fixes

* fix: add `psycopg[binary]` in requirements.txt and .env example file cleanup. ([`49408ce`](https://github.com/vasilistotskas/grooveshop-django-api/commit/49408ce1e80fca4ae449c9994cf3664aed8e38ad))

* fix(config): `SITE_ID` default value fix ([`b40d7ba`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b40d7ba4f940c34626e6f8f6db6d2d1b72d6b473))

### Features

* feat: More at `Description`

- settings.py split in /config folder.
- `phonenumber_field` library and use of `PhoneNumberField` in models and serializers.
- `pyproject.toml` clean up.
- migrations update. ([`1c4d811`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1c4d81115d7495ee7e6752793836aa0ec0841434))

## v0.34.0 (2023-10-19)

### Features

* feat(ci): Update package versions and use cache, compile locales ([`dee4d0f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/dee4d0f9737f3a9c695062482a3112de0bc2a020))

## v0.33.1 (2023-10-18)

### Bug fixes

* fix(Dockerfile): copy python version 3.12 ([`1bae2ab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1bae2ab8e873697418b1de6a36c81cf779f1bfd6))

## v0.33.0 (2023-10-18)

### Features

* feat: More at `Description`

- python version bump to `3.12.0`
- new lib `djmoney` add and usage in models and serializers.
- versions bump.
- migrations and api schema generate. ([`06faa95`](https://github.com/vasilistotskas/grooveshop-django-api/commit/06faa9547dba35b3c02e65e6849d8aa1afa5822d))

## v0.32.0 (2023-10-17)

### Bug fixes

* fix: `settings.py` change `TIME_ZONE` default and add multilang at `test_serializers.py`. ([`d9628e8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d9628e8992d8225dc88bc97ebd4455d0440c69af))

* fix(settings.py): update env defaults. ([`b371658`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b371658d6a5c5423bc8f5d15595805528edfab3c))

* fix(settings.py): languages update. ([`95b2aee`](https://github.com/vasilistotskas/grooveshop-django-api/commit/95b2aeed53178f8b61bab06f01bb3ed5d6184fed))

* fix(settings.py): languages update. ([`9fc02fd`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9fc02fd2b40960dad89831a63863ac0d54374056))

* fix(settings.py): `APPEND_SLASH` default true. ([`62e519a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/62e519acaa6f3461f79d66734f0d48b4f272c9c3))

* fix(settings.py): add defaults at env vars. ([`4bab314`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4bab314c9c4c346185168423060be3d8765e2900))

* fix(settings.py): local static path for `GITHUB_WORKFLOW` system env ([`b113b14`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b113b14bda905889bcba113c186f1f214a7c3a90))

* fix(settings.py): `ALLOWED_HOSTS` env variable default set. ([`d0b0d4a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d0b0d4a5fae05ba7fece9d1b275dc22439ef8c95))

### Features

* feat: More at `Description`

- Env files updated.
- Replace `django-environ` lib with `python-dotenv`.
- Multi factor authentication API endpoints implemented.
- Settings and core/storages.py class for production S3 AWS storage.
- Versions bump.

More fixes and improvements. ([`8d4ff27`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8d4ff27168131749ead41fc001092dc8ed5bb72d))

## v0.31.0 (2023-10-06)

### Bug fixes

* fix: session serializer camel case fixed. ([`18baf25`](https://github.com/vasilistotskas/grooveshop-django-api/commit/18baf256d87e2d5c334b5f84dd59332714537ddb))

### Features

* feat: More at `Description`

- Authentication improvement. `signal` receive after social account signed up to populate account image.
- Authentication new API endpoint `is_user_registered` based on email.
- Authentication `AuthenticationAllAuthPasswordResetForm` method overriding `AllAuthPasswordResetForm` to change domain.
- postgres in `ci.yml` version bump from `13` to `16`.
- More lint and general fixes. ([`53cc219`](https://github.com/vasilistotskas/grooveshop-django-api/commit/53cc219b05856d48abc1f39cf8f733d51a20cfa6))

## v0.30.0 (2023-10-05)

### Features

* feat: More at `Description`

- `Versions bump`: Django, coverage, pylint, charset_normalizer, urllib3, rich, django-redis remove,
- `psycopg2-binary` remove and replace with fresher and recommended `psycopg[binary]`
- User auth account adapter override
- Model constraints to modern way
- Migrations update ([`e3d1703`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e3d170381c44c2570f027b9b6d48816d6d4add61))

## v0.29.1 (2023-10-03)

### Bug fixes

* fix: Cart service init fix, migrations update. ([`4d48e88`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4d48e88b104710b34f265af6446a8bb062529112))

### Unknown

* Chore: Versions bump, migrations update, API schema generate, notification app fix. ([`0553779`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0553779988f84ec92fad401dc31e1eadaadc5246))

## v0.29.0 (2023-10-03)

### Features

* feat: `settings.py` updated, Session auth app refactor, User model improve.

- Session: Remove method `ensure_cart_id` at session middleware, new API endpoints (`session-all`, `session-refresh`, `session-revoke`, `session-revoke-all`, `session-active-users-count`, `session-refresh-last-activity`), signal updated,
- Settings: Update CORS and Auth.
- User: Model new method `remove_session` and properties `get_all_sessions` and `role`. ([`a142372`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a142372c233207206f52e6aa8fe1c7a1d1c28977))

* feat: Cart service refactor and cleanup, tests and API view updated. ([`de29e5e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/de29e5e208ac3382585a72b4fd9e47ea7aef9e25))

## v0.28.0 (2023-09-27)

### Bug fixes

* fix(session/middleware.py): `update_cache` method rename to `update_session` to also set session + cache. ([`27c6a5d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/27c6a5d4a24f1d3ed08a52a8c9afdbcbf3584042))

### Features

* feat: Auth module improve, __unicode__ and __str__ method fallback, session and cache rewrite. ([`cc21594`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cc215943984350530d6b11c3ee0f78fb6c06035b))

## v0.27.0 (2023-09-23)

### Features

* feat(Faker): Increase command run speed using bulk_create, time of execution for each command add.

- Run migrations ([`9f56577`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9f5657773827ff76158f8e63b73f9dba16589f7d))

## v0.26.2 (2023-09-23)

### Bug fixes

* fix(populate_user_address): Command fix check for main address. ([`770f6fb`](https://github.com/vasilistotskas/grooveshop-django-api/commit/770f6fbfe2c12ec13b1ce2d351edf4d14a07ec08))

## v0.26.1 (2023-09-20)

### Bug fixes

* fix(cart): Serializer create method fix using try catch. ([`1391aac`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1391aac5730a6ef85de8bcc9f005cec81613acc9))

### Chores

* chore: Versions bump.

- pillow
- django-filter
- python-semantic-release
- dj-rest-auth
- urllib3
- pydantic
- rich ([`00f4489`](https://github.com/vasilistotskas/grooveshop-django-api/commit/00f448946aae902f94296f0e52693a6bce155bee))

## v0.26.0 (2023-09-18)

### Chores

* chore: Improvements.

- Env updated.
- Gitignore Update.
- Remove django-sql-explorer.
- README Cleanup.
- API schema generate.
- Locales update.
- .idea update. ([`4622f0c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4622f0cb02cec6364e46d04a9b4482c31be43d4f))

### Features

* feat(search): Search improvements.

- Added 001_pg_trigram_extension.py in core.
- Search functionality and performance improve.
- Run migrations. ([`2436d9a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2436d9a987b1f863f0785fec3a2d26f7615b995d))

* feat(search): Search improve.

- Pass weights on product_post_save signal.
- ProductQuerySet fixed, weights pass fixed.
- Api View return a lot of better reasults included `results`, `headlines`, `search_ranks`, `result_count` and `similarities`
- SearchProductSerializer and SearchProductResultSerializer implemented. ([`da8ee4b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/da8ee4bed192ef23fa4dd1c4681108ff229b67bc))

* feat: Celery, uvicorn and Notifications.

- celery.py rename to celery_setup.py.
- Create uvicorn_server.py to be able to run with uvicorn.
- Notification feature.
- settings.py improvements ([`bbc5f45`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bbc5f45cc25b6f1881c752fb48ffafc90f79d5a0))

* feat: Changes to naming, cleanup, AddressModel change and migrations.

- App urls cleanup.
- UserAccount usage from import replaced with get_user_model().
- Model `related_name` usage improve for readability.
- UserAddress Model add contstraint.
- Run migrations. ([`5a3f590`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5a3f590c4e91a1abdeee65e187b46516fb08ed33))

## v0.25.0 (2023-09-13)

### Features

* feat: Versions bump, add allauth `AccountMiddleware` in settings.py. ([`4b42395`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4b423958945db60ccc83014ca0b1b73c1e447475))

## v0.24.0 (2023-09-10)

### Features

* feat: Auth api endpoints and views + packages bump. ([`edc306e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/edc306e74145203d68fdb8856149cd93686bb193))

## v0.23.0 (2023-09-04)

### Features

* feat: Fixes and versions bump.

- Remove `translation` fields from API filtering and ordering.
- Auth and Permissions add to user API view.
- `DEFAULT_PERMISSION_CLASSES` , `DEFAULT_THROTTLE_CLASSES` and `DEFAULT_THROTTLE_RATES` added. ([`3538ee6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3538ee6d4d856f83e86c1e48dc919edcbe199536))

## v0.22.0 (2023-09-02)

### Chores

* chore(schema.yml): Generate schema.yml ([`6500b80`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6500b80ab6bc00cd0b56a35497cc68a397c584c3))

### Features

* feat(Notifications): Notifications App implemented, Ui for home page prototype.

Change, CELERY_BROKER_URL and CELERY_RESULT_BACKEND envs.
Added CHANNEL_LAYERS in settings.py.
Celery New task debug_task_notification.
asgi ProtocolTypeRouter added for websocket.
websocket_urlpatterns with `ws/notifications/` path in urls.py.
NotificationConsumer implemented.
README.md updated.
Make new migration files. ([`833116f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/833116fd8646090e6b632a8bfe1b6b50146512ac))

## v0.21.1 (2023-09-01)

### Bug fixes

* fix: User address model integer enum choices fields from CharField to PositiveSmallIntegerField, added translated description for IntegerChoices. ([`4c7e390`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4c7e390bd064f93303b2b26135a2c6eb3550016a))

## v0.21.0 (2023-09-01)

### Features

* feat(enums): Replace enum and usage with django built in models.TextChoices and models.IntegerChoices

Versions bump.
Locales compile.
Run Migrations.
Api schema.yml updated. ([`1897e29`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1897e298d73183524847d00eb26f95c2cd8e915e))

## v0.20.0 (2023-08-29)

### Features

* feat: Search improved, versions bump, minor fixes/improvements.

1) Default db postgres, added DB_PORT new env var, and update name DB_PASS env var to DB_PASSWORD.
2) Country model translations name field max_length change.
3) Order model country and region field on delete set null.
4) populate_country command fixed.
5) Faker and python-semantic-release versions bump.
6) Product fields `discount_value`, `final_price`, `price_save_percent` calculation removed from save method and implement signal and method update_calculated_fields.
7) ProductQuerySet: new methods update_search_vector for search usage and update_calculated_fields.
8) Product Search prototype and tests implemented. ([`bff87a5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bff87a57d1851cd5b316a3b941ad59d99e067945))

## v0.19.0 (2023-08-29)

### Features

* feat(commands): Seed commands improvements.

1) total var name change based on options total name.
2) faker seed instance for each lang using different seed.
3) check for available_languages.
4) checks for models with unique together. ([`721bf6a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/721bf6a83cf54ca6ee485c8dc4f7ed067e71a600))

## v0.18.0 (2023-08-27)

### Bug fixes

* fix(search): Search improve. ([`1217337`](https://github.com/vasilistotskas/grooveshop-django-api/commit/12173371d76eb7042025235decbf4516e423d508))

### Chores

* chore(static): Added static folder ([`cd8359f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cd8359fbdbff35162248e1348f62594234eb83c8))

### Features

* feat(tests): Typing improvements and tearDown class added in tests. ([`bfee3c3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/bfee3c3c3a00008e1ee3848d406fbca5c94adefe))

* feat(tests): Create tests for simple search and make_thumbnail method. ([`7c7fbe6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7c7fbe672e24bbf7c19c3b374d42ba16ec9a5436))

## v0.17.0 (2023-08-26)

### Features

* feat(settings): Security improved, minor type cast at populate_user_address command. ([`8f43775`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8f4377596401975ae521b3c2e24622e4eddf65c6))

## v0.16.0 (2023-08-25)

### Features

* feat(seed): Add new dump images for product image seeding, refactor method get_or_create_default_image to be also able to create image from non /media path. ([`71b9790`](https://github.com/vasilistotskas/grooveshop-django-api/commit/71b979021a5e0356d728d50e5de14355f08b03b1))

## v0.15.0 (2023-08-25)

### Chores

* chore: migration files and compile locales. ([`9889b75`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9889b75fa27f9f1d856f4a51fa16cf096171b921))

### Features

* feat(enum): New enum for order document type and move pay way enum in pay way app. ([`6066e4a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6066e4a52313f5703979f6deaf16f78553ae5db4))

## v0.14.2 (2023-08-24)

### Bug fixes

* fix: Rename StatusEnum in order and review.

order status: StatusEnum -> OrderStatusEnum
review status: StatusEnum -> ReviewStatusEnum ([`1b623c8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1b623c88372d42e6c4ba1d9116060dd75ae93832))

## v0.14.1 (2023-08-24)

### Bug fixes

* fix(templates): Change template dir to core/templates. ([`3a57eb5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3a57eb57e0abeafac98c68d6122a644874c06952))

## v0.14.0 (2023-08-24)

### Features

* feat(pagination): Cursor pagination fixed and tests implemented. ([`441078e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/441078eea389a82ce50183ccfe077cb397722122))

## v0.13.0 (2023-08-23)

### Bug fixes

* fix: Lint and PEP fixes and generate_random_password method improved ([`5d74052`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5d74052c62e7f8e663b97804cc76895c7ffa663b))

### Chores

* chore(order): Migrations for paid_amount field. ([`da8276c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/da8276cff863e5e9dd294d46eb9e6f10c6f0bf0e))

* chore: requirements.txt and pyproject.toml update

Packages Updated:
django-allauth==0.54.0 to 0.55.0
click==8.1.6 to 8.1.7 ([`b4a61a3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b4a61a3da44ee44234b51fc0923b5898be40a265))

* chore: Remove useless folders ([`705c306`](https://github.com/vasilistotskas/grooveshop-django-api/commit/705c306b49b871e05baea087c08f032de1534f87))

### Features

* feat(testing): New unit tests.

Tests for generate_schema_multi_lang method implemented.
Tests for BaseExpandSerializer class implemented.
Tests for PascalSnakeCaseOrderingFilter class implemented. ([`355ae53`](https://github.com/vasilistotskas/grooveshop-django-api/commit/355ae533d4f5b2d9fdb368db10f7312af847292f))

## v0.12.0 (2023-08-17)

### Features

* feat: New Unit tests implemented and remove unused methods and files. ([`db41415`](https://github.com/vasilistotskas/grooveshop-django-api/commit/db414155306e2284952391e156291cee28dbf9a5))

## v0.11.4 (2023-08-17)

### Bug fixes

* fix(ci.yml): Publish package distributions to TestPyPI if released. ([`f11972b`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f11972b8a50cc61d55febaa66ec2a06028b6f986))

### Chores

* chore(README.md): Added Coverage Status ([`0d30ec0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0d30ec06ba4293a842a0ec3fc470e532f9b3f501))

## v0.11.3 (2023-08-17)

### Bug fixes

* fix(ci.yml): Remove upload-to-gh-release step ([`8540ef8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8540ef884dc60e9b63f3d5a4318cfcb67635c64e))

## v0.11.2 (2023-08-17)

### Bug fixes

* fix(ci.yml): Use GITHUB_TOKEN and add tag ([`d0f14ef`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d0f14ef27d22f14c1aaf8ef89270353ae1786eed))

### Chores

* chore(app): Added static folder ([`6d6b287`](https://github.com/vasilistotskas/grooveshop-django-api/commit/6d6b28797336bcc0805f5857af2f2b3a9d1c6481))

## v0.11.1 (2023-08-17)

### Bug fixes

* fix(poetry): update python-semantic-release in requirements and pyproject. ([`4ca745d`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4ca745d011a4362f0d427784d65c3e78c0712533))

## v0.11.0 (2023-08-17)

### Bug fixes

* fix(tests): Testing fixes ([`50f75dc`](https://github.com/vasilistotskas/grooveshop-django-api/commit/50f75dce38cd44bc845a7537f58bdc99a461467a))

* fix(caches): fix empty caches at settings.py ([`287d2ed`](https://github.com/vasilistotskas/grooveshop-django-api/commit/287d2edfa13be5aff41fc2630da3a8f6eb3fe690))

* fix(app): caches dont use fallback in github env, schema.yml generate and added serializer_class in order checkout api view ([`9abb939`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9abb93923050044ced20327b3c7e1f35417ba394))

### Features

* feat(tests): Rewrite Cart tests/service and session middleware code cleanup. ([`02effab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/02effabade8e9e389a56c66e0eca90d60fb1c92d))

### Refactoring

* refactor(app): Changes in Description

1) Tests implemented and improved for application.
2) Add name in all URL paths (reverse) usage.
3) Cache Refactor.
4) Multi language translations.
5) requirements.txt and pyproject.toml update. ([`da506b6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/da506b696f4b08eae15ac020c72a560d8cb02c9e))

## v0.10.1 (2023-07-27)

### Bug fixes

* fix(ci.yml): Build the dist_build dir correctly ([`5977d77`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5977d779e5dcf841994dc3d877e91b711de9b597))

### Chores

* chore(requirements.txt): update requirements

python-semantic-release==8.0.4
drf-spectacular==0.26.4
pylint==2.17.5 ([`ac04533`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ac0453383e49a8448d5f4e33408eae3e8ac374ad))

* chore(tests): Blog test minor update ([`d0728e3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d0728e38bc9c4b3d03c17c0c7fc05d9fc549b9c9))

## v0.10.0 (2023-07-26)

### Features

* feat(tests): Rewrite Blog tests ([`d8e682a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d8e682afa0855af633118b65aefd615d8fb6d5e6))

## v0.9.2 (2023-07-24)

### Bug fixes

* fix(templates/CHANGELOG.md): Remove auto generated template from semantic release changelog, meaby will be created again , trying to figure it out. ([`74312e6`](https://github.com/vasilistotskas/grooveshop-django-api/commit/74312e67403286537c386505f60d44e7a656bcca))

## v0.9.1 (2023-07-24)

### Bug fixes

* fix(templates/CHANGELOG.md.j2): Update file ([`fbb5dde`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fbb5dde10d51fab17ab0e0ee07a443e125265ce8))

## v0.9.0 (2023-07-24)

### Features

* feat(pyproject.toml): Added changelog settings and remove generated templates in project src ([`0f1ff84`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0f1ff8433c695f4110677edd7c1ba874e49bc603))

## v0.8.9 (2023-07-23)

### Bug fixes

* fix(pyproject.toml, setup.py): Update semantic release configuration ([`f49184a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f49184a2f3b4c14d44b7ebb2eab8669c29e0c37d))

### Chores

* chore: run poetry lock ([`03bf2ab`](https://github.com/vasilistotskas/grooveshop-django-api/commit/03bf2ab87edb481ffd0560eef6570c3b72d0c638))

### Unknown

* Delete openid directory ([`912a198`](https://github.com/vasilistotskas/grooveshop-django-api/commit/912a1988b323aae4fd5a508851de0ef4dfe67b0a))

* Delete account directory ([`73e76aa`](https://github.com/vasilistotskas/grooveshop-django-api/commit/73e76aac0ee7a6a7117a72414b67ca49780be2a1))

* Delete socialaccount directory ([`7353e25`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7353e25afce8cdf025e75891b2ebd5733c3aa2be))

* Delete allauth_2fa directory ([`cdb0d0a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/cdb0d0ab2239a732156f2531640c30d5bd4bb248))

## v0.8.8 (2023-07-22)

### Bug fixes

* fix(ci.yml): remove artifacts from upload-to-gh-release and remove parser options for pyproject.toml semantic_release ([`fffbdc4`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fffbdc488562785b067bccc530fe9f07a3860e9f))

## v0.8.7 (2023-07-22)

### Chores

* chore(setup.py): version bump ([`16b0593`](https://github.com/vasilistotskas/grooveshop-django-api/commit/16b0593f15c073392b2f3e83110daee1c8fa4065))

### Unknown

* 0.8.7

Automatically generated by python-semantic-release ([`b7ae230`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b7ae230b5a4b941a6469102f2fd60d1f9c0e3725))

## v0.8.6 (2023-07-22)

### Bug fixes

* fix(requirements.txt): Remove poetry ([`31f7247`](https://github.com/vasilistotskas/grooveshop-django-api/commit/31f7247138bd71f282fc5b43b39adf984d7c9b4d))

* fix(ci.yml): Trying to build package distributions

Bump urllib3 version.
Bump python-semantic-release version.
Add with packages-dir: dist_build/ at "Publish distribution 📦 to PyPI" step.
Add with packages-dir: dist_build/ at "Publish package distributions 📦 to GitHub Releases" step. ([`3a3b778`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3a3b778427279b37d7318720c0991957cb427a1e))

### Unknown

* 0.8.6

Automatically generated by python-semantic-release ([`b31c0f7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b31c0f7f1a825a3a3fd71cdc8887e7baf8f2c6bb))

## v0.8.5 (2023-07-22)

### Bug fixes

* fix(setup.py): Bump version ([`a0c84a8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a0c84a8da65fb00e6a35e12eb7716623f83dc720))

### Unknown

* 0.8.5

Automatically generated by python-semantic-release ([`23ab7b8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/23ab7b88e920d1a32d5942a3422b1904fed3c86b))

## v0.8.4 (2023-07-22)

### Bug fixes

* fix(ci.yml): Trying to build package distributions ([`f2591ea`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f2591eab8b0717bed08719a0b1f1e811db8165d5))

### Unknown

* 0.8.4

Automatically generated by python-semantic-release ([`ffacb98`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ffacb98ee054c2e76d41ae272e75901972172571))

## v0.8.3 (2023-07-22)

### Bug fixes

* fix(ci.yml): Trying to build package distributions ([`8b70270`](https://github.com/vasilistotskas/grooveshop-django-api/commit/8b70270e6469548a1812c0e1a5bc05de8b822c2f))

### Unknown

* 0.8.3

Automatically generated by python-semantic-release ([`b629ab9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b629ab951270e6b7dc9acc26feacc0cc9dd39924))

## v0.8.2 (2023-07-22)

### Bug fixes

* fix(ci.yml): Fix release to PyPI Upgrade to Trusted Publishing ([`48acc43`](https://github.com/vasilistotskas/grooveshop-django-api/commit/48acc43f244b301282ea51f1a68c79cbd35a0f8b))

### Unknown

* 0.8.2

Automatically generated by python-semantic-release ([`012549e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/012549e21037cda5da729df6f565f1881665cbc5))

## v0.8.1 (2023-07-21)

### Bug fixes

* fix(ci.yml): Update github token ([`b700849`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b7008491df8a863918be938282d6b0bdb123195e))

### Unknown

* 0.8.1

Automatically generated by python-semantic-release ([`1e6cc5e`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1e6cc5e3e1d7a91b364e202e50356a2447d14da4))

## v0.8.0 (2023-07-21)

### Features

* feat(ci.yml): Update release to PyPI ([`9ddb846`](https://github.com/vasilistotskas/grooveshop-django-api/commit/9ddb846dba01361e5f507f120aeb7c5f98c46751))

### Unknown

* 0.8.0

Automatically generated by python-semantic-release ([`5b4d383`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5b4d38386eae6bbb055bf7339d29e6b099614150))

## v0.7.0 (2023-07-21)

### Features

* feat(ci.yml): Update release to PyPI ([`baa010c`](https://github.com/vasilistotskas/grooveshop-django-api/commit/baa010c943223ac4c2473d661e72fb1e224a1eb6))

### Unknown

* 0.7.0

Automatically generated by python-semantic-release ([`a127965`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a1279651c76a4189b7505654800e47b71aabdbef))

## v0.6.0 (2023-07-21)

### Features

* feat(ci.yml): Update release to PyPI ([`74956f8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/74956f8cdfe006873ca80c067fa8656b27534abe))

### Unknown

* 0.6.0

Automatically generated by python-semantic-release ([`3e766fe`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3e766fed6168d13ba150ad067b4254d664af664a))

## v0.5.0 (2023-07-21)

### Features

* feat(ci.yml): Update release ([`1d8ed75`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1d8ed75f10364996d1a4de70e25befda345a09cd))

### Unknown

* 0.5.0

Automatically generated by python-semantic-release ([`14e85c5`](https://github.com/vasilistotskas/grooveshop-django-api/commit/14e85c56035c0c139e5cbcb5535f4839186ed487))

## v0.4.0 (2023-07-21)

### Features

* feat(app): 🚀 .pre-commit-config.yaml versions bump, implement .flake8 file and remove setup.cfg file ([`ec12e48`](https://github.com/vasilistotskas/grooveshop-django-api/commit/ec12e489f9b29d8f1707aed9b1e30ba1e2ce9a28))

### Unknown

* 0.4.0

Automatically generated by python-semantic-release ([`3fa9bbf`](https://github.com/vasilistotskas/grooveshop-django-api/commit/3fa9bbfd14e91187321144f2f190ec663d0f75f3))

## v0.3.2 (2023-07-21)

### Bug fixes

* fix(semantic_release): Replace github token ([`53e68c0`](https://github.com/vasilistotskas/grooveshop-django-api/commit/53e68c0f30b4e3343ed54f3e8aa567771ad91755))

### Unknown

* 0.3.2

Automatically generated by python-semantic-release ([`94b0908`](https://github.com/vasilistotskas/grooveshop-django-api/commit/94b0908526e68cccf44dcae7d064934955908199))

## v0.3.1 (2023-07-21)

### Bug fixes

* fix(semantic_release): Trying to make v8 work. ([`1c7ba51`](https://github.com/vasilistotskas/grooveshop-django-api/commit/1c7ba515a27705f7b53970bf1c447d63ecc3ddbd))

* fix(app): Update ci.yml github workflow and remove useless folders.

ref: https://python-semantic-release.readthedocs.io/en/latest/migrating_from_v7.html ([`d65a84f`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d65a84f3ffa5de34d7a399825b4b789af3539e66))

### Unknown

* 0.3.1

Automatically generated by python-semantic-release ([`159cd08`](https://github.com/vasilistotskas/grooveshop-django-api/commit/159cd084851b0dc61af5fc528968d10536e4bcf3))

## v0.3.0 (2023-07-21)

### Features

* feat(seed): Seeders refactor and some models split to different files

Plus minor fixes ([`5220477`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5220477ea35897953fb98476d60757800ea6a25c))

## v0.2.2 (2023-07-19)

### Bug fixes

* fix(test_model_blog_tag.py): Test fix and re run migrations ([`fd076a9`](https://github.com/vasilistotskas/grooveshop-django-api/commit/fd076a9eebdd5f3730e2021b24cf534232b01ea7))

* fix(poetry): Poetry lock, resolve package errors, lint fix and compile messages ([`7406da2`](https://github.com/vasilistotskas/grooveshop-django-api/commit/7406da2de4eb9c16c4213ea41aa4dd2b981705d5))

### Chores

* chore(LICENSE.md): Added ([`e532056`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e532056405ebd7703f8fa24fd3c464fa94a75381))

### Features

* feat(Localization): Implement multi language support in app, admin and API

* New libs
django-rosetta UI to update locales in path /rosetta.
django-parler/django-parler-rest for multi language models/serializers and API.
* Env update
* Commands for localization
django-admin makemessages -l <locale>
django-admin makemessages --all --ignore=env
django-admin compilemessages --ignore=env ([`e926e53`](https://github.com/vasilistotskas/grooveshop-django-api/commit/e926e5310141e73799e09fbc6633537a4a2be8ec))

### Unknown

* 0.2.2

Automatically generated by python-semantic-release ([`c68403a`](https://github.com/vasilistotskas/grooveshop-django-api/commit/c68403a0aaf29441f3020dda1e9beaeb3cc5bff3))

## v0.2.1 (2023-07-07)

### Bug fixes

* fix(docker.yml): image fix ([`b8058de`](https://github.com/vasilistotskas/grooveshop-django-api/commit/b8058dee7d98f0ec3fd8a670330e259642a62fe2))

### Unknown

* 0.2.1

Automatically generated by python-semantic-release ([`5541270`](https://github.com/vasilistotskas/grooveshop-django-api/commit/5541270faee5c961de5513f24bddee736dd0a42a))

## v0.2.0 (2023-07-07)

### Features

* feat(docker): Added docker file and more minor fixes

Remove BACKEND_BASE_URL and replace usage with APP_BASE_URL ([`f6706d8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/f6706d852aa993a3dcf7d684e01439011382b7ad))

### Unknown

* 0.2.0

Automatically generated by python-semantic-release ([`0a74685`](https://github.com/vasilistotskas/grooveshop-django-api/commit/0a746856b2bbdee1bae8770863f325049ce9f0ff))

## v0.1.1 (2023-07-07)

### Bug fixes

* fix(build): pyproject.toml, setup.cfg and setup.py

Fixed ([`a7f6638`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a7f6638ba3ac685984daa0fa0d040c448d5e1899))

### Unknown

* 0.1.1

Automatically generated by python-semantic-release ([`a466a25`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a466a259a385e6868584c1ec600d4aa84b170015))

## v0.1.0 (2023-07-07)

### Bug fixes

* fix(ci.yml): env github token fix for coveralls

replace DJANGO_GITHUB_TOKEN with GITHUB_TOKEN ([`06be142`](https://github.com/vasilistotskas/grooveshop-django-api/commit/06be142b166f74b951a95709fb6f43d19a260f2c))

* fix(workflows): Update githubtoken ([`42346f8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/42346f82393625c2e0dfa9fb19033e1afb5ddae0))

* fix(branch): Set branch to master

At files: pyproject.toml and setup.cfg ([`a858284`](https://github.com/vasilistotskas/grooveshop-django-api/commit/a858284abde2ff492667b13a01e202f4960f0d13))

* fix(ci.yml): change on push to main ([`4666ec7`](https://github.com/vasilistotskas/grooveshop-django-api/commit/4666ec72bba4d76cbd3748760a6a40a198343164))

### Chores

* chore(logs): Add missing files ([`36e03f3`](https://github.com/vasilistotskas/grooveshop-django-api/commit/36e03f3b970c55967f2677e3675488af2a5fc336))

* chore(static): Remove generated static files ([`d5fba15`](https://github.com/vasilistotskas/grooveshop-django-api/commit/d5fba150e726d0a880328d6fc1b6db4e9cc984db))

### Features

* feat(docker.yml): Added new docker.yml file for github workflows

Push Docker image ([`2a039e8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/2a039e8be63b991bdd6487ff99b49038a293f6d6))

### Unknown

* 0.1.0

Automatically generated by python-semantic-release ([`0763761`](https://github.com/vasilistotskas/grooveshop-django-api/commit/07637614df26aa3cf4339d4a4babc7289afed0ff))

* fix:(lint): lint fixed and versions set to 0 ([`de8f3b8`](https://github.com/vasilistotskas/grooveshop-django-api/commit/de8f3b8e015cb42ba01a35385312aa98ff8d6ba8))

* Initial Commit ([`399c796`](https://github.com/vasilistotskas/grooveshop-django-api/commit/399c796fb95248c8fb916708a6316d57b0e3fb40))
