"""External services: IndexNow, payments, carriers, Meta and the agent gateway."""

from os import getenv

from django.core.exceptions import ImproperlyConfigured

from settings.env import SYSTEM_ENV

# IndexNow (``indexnow/``): the endpoint the batched notification is posted
# to (the protocol's shared endpoint, which hands the URLs to every
# participating search engine) and how long one POST may take.
INDEXNOW_ENDPOINT = getenv(
    "INDEXNOW_ENDPOINT", "https://api.indexnow.org/indexnow"
)
INDEXNOW_TIMEOUT_SECONDS = int(getenv("INDEXNOW_TIMEOUT_SECONDS", "15"))

# PAYMENT SETTINGS
# Stripe Configuration (dj-stripe format)
# NOTE: there is deliberately NO STRIPE_LIVE_SECRET_KEY /
# STRIPE_TEST_SECRET_KEY / STRIPE_LIVE_MODE / STRIPE_PUBLISHABLE_KEY
# here — the platform-account concept is gone. Every tenant's Stripe
# identity (secret + publishable key) lives ONLY on the ``Tenant`` row
# (see ``tenant/credentials.py:stripe_credentials()``), and each
# tenant schema carries its own dj-stripe ``APIKey``/``WebhookEndpoint``
# rows provisioned by ``manage.py bootstrap_stripe`` — those per-schema
# rows are the real key source, not a Django setting. dj-stripe itself
# tolerates the absence of these settings names entirely (verified
# against the installed 2.11.0: every read is a bare
# ``getattr(settings, "...", default)``, never a hard ``hasattr``
# requirement), so there is nothing to define here even as an
# always-empty placeholder.
DJSTRIPE_FOREIGN_KEY_TO_FIELD = "id"
DJSTRIPE_WEBHOOK_VALIDATION = "verify_signature"
# Webhook verification is per-schema: dj-stripe's UUID-routed webhook
# view verifies each delivery against the ``WebhookEndpoint`` ROW secret
# provisioned by ``manage.py bootstrap_stripe``. ``DJSTRIPE_WEBHOOK_SECRET``
# is NOT defined here — dj-stripe 2.11.0 never reads that setting name
# anywhere in its source (confirmed by inspection); it only appears in
# an informational system-check hint string. Fully vestigial — no code
# path depends on it.

# STRIPE_API_VERSION is intentionally NOT set. dj-stripe pins its own
# DEFAULT_STRIPE_API_VERSION to match its Django model schema and uses that
# for ALL Stripe communication, including webhook processing; the dj-stripe
# docs state the value "should not be changed" (api_versions.md). Overriding
# it forces dj-stripe to parse payloads shaped for one API version against
# models built for another, silently corrupting the local Stripe mirror.
# Ops: any manually-created Stripe Dashboard webhook endpoint should use the
# account's current API version so its events match dj-stripe's schema.
STRIPE_WEBHOOK_DEBUG = getenv("STRIPE_WEBHOOK_DEBUG", "false").lower() == "true"

# Viva Wallet Configuration
# Per-merchant identity (merchant ID, API key, OAuth2 client id/secret,
# webhook verification key, source code, live/demo mode) lives ONLY on
# the ``Tenant`` model (see ``tenant/credentials.py:viva_wallet_
# credentials()``) — there is no platform-wide env-var fallback. Money
# must never silently route through credentials the operator did not
# explicitly configure for THIS tenant.


# ---------- BoxNow Shipping ----------
# Per-merchant identity (OAuth2 client id/secret, partner ID, warehouse
# ID, notify phone, webhook secret) AND the environment those keys belong
# to (``Tenant.box_now_live_mode``) live ONLY on the ``Tenant`` model (see
# ``tenant/credentials.py:box_now_credentials()``) — no platform fallback.
# BoxNow issues separate stage and production keys and each only
# authenticates against its own API, so the platform carries BOTH hosts and
# the tenant's flag picks one (``shipping_boxnow/client.py``).
BOXNOW_PRODUCTION_API_BASE_URL = getenv(
    "BOXNOW_PRODUCTION_API_BASE_URL", "https://api-production.boxnow.gr"
)
BOXNOW_PRODUCTION_LOCATION_API_BASE_URL = getenv(
    "BOXNOW_PRODUCTION_LOCATION_API_BASE_URL",
    "https://locationapi-production.boxnow.gr",
)
BOXNOW_STAGE_API_BASE_URL = getenv(
    "BOXNOW_STAGE_API_BASE_URL", "https://api-stage.boxnow.gr"
)
BOXNOW_STAGE_LOCATION_API_BASE_URL = getenv(
    "BOXNOW_STAGE_LOCATION_API_BASE_URL",
    "https://locationapi-stage.boxnow.gr",
)
# Staging mirrors the production tenant rows, so a refresh brings the
# live flag (and the live keys) with it. The deployment named ``staging``
# therefore refuses to talk to the production BoxNow API at all: a stale
# flag fails loudly instead of booking a real parcel.
BOXNOW_LIVE_MODE_ALLOWED = SYSTEM_ENV != "staging"
# Shipping price + free-shipping threshold are NOT mirrored as Django
# settings: they live in `extra_settings.Setting` rows (BOXNOW_SHIPPING_PRICE
# / BOXNOW_FREE_SHIPPING_THRESHOLD). Both Django (calculate_shipping_cost)
# and Nuxt (`/api/settings/get`) read from there so an admin can retune the
# rate without a redeploy.
BOXNOW_HTTP_TIMEOUT = int(getenv("BOXNOW_HTTP_TIMEOUT", "10"))

# ---------- ACS Shipping ----------
# Per-merchant identity (API key, Company_*/User_* credentials, billing
# code, station origin) lives ONLY on the ``Tenant`` model (see
# ``tenant/credentials.py:acs_credentials()``) — no platform fallback.
# Only transport/platform config (base URL, timeout, stale-shipment
# threshold) stays here — which countries ACS ships to is now a
# per-tenant ``ShippingRate`` question, not a platform one.
ACS_API_BASE_URL = getenv(
    "ACS_API_BASE_URL",
    "https://webservices.acscourier.net/ACSRestServices/api/ACSAutoRest",
)
ACS_HTTP_TIMEOUT = int(getenv("ACS_HTTP_TIMEOUT", "15"))
# Requests in flight per ACS account across every pod
# (``shipping_acs/concurrency.py``). ACS answers 406 to overlapping calls
# and publishes no figure, so the default is our own measurement: 10 days
# of production logs (2026-10-11 to 10-20), each tracking task against how
# many ACS tasks were in flight at its start, gave a rejection rate of 0.9%
# at 1, 2.0% at 2, 3.6% at 3, 7.9% at 6, 17% at 8 and 19-50% at 9 or more
# (table in ``docs/order/shipping-acs.md``). 2 halves the rate against 3; with
# the reserve below the polls run one at a time and still finish inside
# their tick, see the spacing.
ACS_MAX_CONCURRENT_REQUESTS = int(getenv("ACS_MAX_CONCURRENT_REQUESTS", "2"))
# How long a call waits for a free slot before it is retried through Celery.
ACS_SLOT_WAIT_SECONDS = int(getenv("ACS_SLOT_WAIT_SECONDS", "10"))
# Slots the background tracking polls may NOT take, so a shopper's call
# (checkout price quote, address validation) always finds one free instead
# of waiting behind the batch. Polls then run on the remaining slots only.
ACS_INTERACTIVE_RESERVED_SLOTS = int(
    getenv("ACS_INTERACTIVE_RESERVED_SLOTS", "1")
)
if not 0 <= ACS_INTERACTIVE_RESERVED_SLOTS < ACS_MAX_CONCURRENT_REQUESTS:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "ACS_INTERACTIVE_RESERVED_SLOTS must leave the polls at least one "
        "of ACS_MAX_CONCURRENT_REQUESTS slots."
    )
ACS_POLL_SLOTS = ACS_MAX_CONCURRENT_REQUESTS - ACS_INTERACTIVE_RESERVED_SLOTS
# Measured mean duration of one ``poll_acs_tracking_one`` (two ACS calls),
# same logs. The poll slots serve ``poll slots / duration`` polls a second,
# so the batch dispatches one every ``duration / poll slots`` seconds (2 s
# at the defaults, about 400 s for the 200-shipment cap, inside the 15
# minute tick) and the polls never queue for a slot longer than
# ``ACS_SLOT_WAIT_SECONDS``.
ACS_POLL_AVERAGE_TASK_SECONDS = float(
    getenv("ACS_POLL_AVERAGE_TASK_SECONDS", "2.0")
)
ACS_POLL_DISPATCH_SPACING_SECONDS = (
    ACS_POLL_AVERAGE_TASK_SECONDS / ACS_POLL_SLOTS
)
# Days without a tracking event before a non-terminal shipment is
# reported by check_stale_acs_shipments to the store's operators
# (platform ADMINS only as a fallback).
ACS_STALE_SHIPMENT_DAYS = int(getenv("ACS_STALE_SHIPMENT_DAYS", "3"))
# A voucher request that ends with an unknown outcome holds the store's
# further voucher requests (``shipping_acs.voucher_hold``): ACS is failing
# and each request sent would be one more order to check at its portal.
# Seconds after the last lost answer before ACS is asked whether it answers
# again; every first request that fails too doubles the wait, up to the cap.
ACS_VOUCHER_HOLD_RETRY_SECONDS = int(
    getenv("ACS_VOUCHER_HOLD_RETRY_SECONDS", "300")
)
ACS_VOUCHER_HOLD_RETRY_MAX_SECONDS = int(
    getenv("ACS_VOUCHER_HOLD_RETRY_MAX_SECONDS", "3600")
)
# Days an ``AcsApiCall`` history row is kept (request/response of every
# ACS call, recipient details included) before the retention sweep
# deletes it.
ACS_API_CALL_RETENTION_DAYS = int(getenv("ACS_API_CALL_RETENTION_DAYS", "90"))
# Days a SUCCESSFUL tracking read (``ACS_Trackingsummary`` and
# ``ACS_TrackingDetails``, the 15-minute poll: 85% of the rows) is kept.
# Nothing is written to ACS by it and a failure keeps the full period
# above, so a short period costs no evidence.
ACS_API_CALL_POLL_RETENTION_DAYS = int(
    getenv("ACS_API_CALL_POLL_RETENTION_DAYS", "14")
)
# Days a carrier's tracking checkpoints (``AcsTrackingEvent``,
# ``BoxNowParcelEvent``) are kept once the shipment is in a terminal
# state. Long enough for a delivery dispute or a returned-parcel claim.
CARRIER_TRACKING_EVENT_RETENTION_DAYS = int(
    getenv("CARRIER_TRACKING_EVENT_RETENTION_DAYS", "730")
)
# A BoxNow webhook event whose signed time is older than this is ignored.
# The ``BoxNowParcelEvent`` rows are the replay guard, so the window must
# end before the sweep above can delete a row an event could still meet.
BOXNOW_WEBHOOK_MAX_EVENT_AGE_DAYS = int(
    getenv("BOXNOW_WEBHOOK_MAX_EVENT_AGE_DAYS", "90")
)
if BOXNOW_WEBHOOK_MAX_EVENT_AGE_DAYS >= CARRIER_TRACKING_EVENT_RETENTION_DAYS:
    raise ImproperlyConfigured(
        "BOXNOW_WEBHOOK_MAX_EVENT_AGE_DAYS "
        f"({BOXNOW_WEBHOOK_MAX_EVENT_AGE_DAYS}) must be shorter than "
        "CARRIER_TRACKING_EVENT_RETENTION_DAYS "
        f"({CARRIER_TRACKING_EVENT_RETENTION_DAYS}): the replay guard rows "
        "must outlive the events the guard admits."
    )
# Days after a COD parcel is DELIVERED before an absent ACS payout is
# reported as overdue. The measured remittance lag is ~4 days, so 10
# leaves headroom and fires only on genuinely stuck money. Nothing else
# watches these: the tracking poll and the stale-shipment alert both
# exclude terminal shipment states, which a delivered parcel is.
ACS_COD_REMITTANCE_ALERT_DAYS = int(
    getenv("ACS_COD_REMITTANCE_ALERT_DAYS", "10")
)
# Pricing AND the countries ACS ships to live in ``shipping.ShippingRate``
# rows now, not here or in ``extra_settings`` — an admin adds a rate to
# add a country, and retunes price/threshold on the same row, no
# redeploy either way. The master on/off switch is still the
# ShippingProvider.is_active flag — see Phase 0
# (shipping/migrations/0002_seed_providers.py).

# ---------- Meta Conversions API ----------
# Days a ``MetaCapiEventLog`` delivery row (status, trace id) is kept.
META_CAPI_EVENT_LOG_RETENTION_DAYS = int(
    getenv("META_CAPI_EVENT_LOG_RETENTION_DAYS", "90")
)
# Pixel ID / dataset ID and the access token are per-tenant secrets —
# they live ONLY on the ``Tenant`` model (see ``tenant/credentials.py:
# tenant_meta_pixel_id()`` / ``tenant_meta_capi_access_token()``), no
# platform fallback. The storefront gets pixel IDs from the tenant
# config (``TenantConfigSerializer``). ``META_CAPI_ENABLED`` in
# extra_settings.Setting is the master kill switch — flipping it False
# stops the Celery dispatcher from posting to Meta without any code
# changes (lets ops shut traffic off during an incident).
META_CAPI_API_VERSION = getenv("META_CAPI_API_VERSION", "v22.0")
# Optional Meta-issued code that flags every event as a test event.
# Set during integration testing so events surface under "Test Events"
# in Events Manager instead of polluting prod attribution. Empty in
# production.
META_CAPI_TEST_EVENT_CODE = getenv("META_CAPI_TEST_EVENT_CODE", "")
# Identifier sent on every CAPI request via ``partner_agent`` so Meta
# can attribute support requests to this integration.
META_CAPI_PARTNER_AGENT = getenv(
    "META_CAPI_PARTNER_AGENT", "grooveshop-django-capi-1.0"
)

# ---------- Agent gateway (grooveshop-agent-gateway) ----------
# Cluster-internal base URL of the agent gateway service. Empty (the
# default) means the agent surface is off: order events are not pushed
# and the gateway-aware cart throttle behaves like a plain anon throttle.
AGENT_GATEWAY_INTERNAL_URL = getenv("AGENT_GATEWAY_INTERNAL_URL", "")
# Shared secret between Django and the gateway, used in BOTH directions:
# sent as ``X-Internal-Token`` on order-event pushes and verified against
# the gateway's ``X-Internal-Gateway`` header on inbound cart mutations.
# Must equal the gateway's ``INTERNAL_EVENTS_SECRET`` env value.
AGENT_GATEWAY_INTERNAL_SECRET = getenv("AGENT_GATEWAY_INTERNAL_SECRET", "")

# Per-request timeout for order-event pushes to the gateway.
AGENT_GATEWAY_HTTP_TIMEOUT = int(getenv("AGENT_GATEWAY_HTTP_TIMEOUT", "5"))
# Master flag for agent-delegated Stripe payments (the ACP/UCP tokenized
# SharedPaymentToken flow). Off until Stripe Agentic Commerce enrollment
# completes — the confirm endpoint returns a clean "not enabled" error.
AGENT_STRIPE_DELEGATED_ENABLED = (
    getenv("AGENT_STRIPE_DELEGATED_ENABLED", "False").lower() == "true"
)
