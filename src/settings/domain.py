"""The domain apps' own limits, windows and retention periods."""

from os import getenv

from django.core.exceptions import ImproperlyConfigured

# Store-edited texts (``texts``). The catalogue of editable texts and the
# check of a store-written one are the storefront's, asked over
# ``NUXT_INTERNAL_BASE_URL`` (``texts.storefront``).
#
# The longest a store's replacement may be. The storefront's own bound is
# 2000 and its longest default under 400, so this is tighter than the
# first and leaves room over the second.
TEXTS_MAX_VALUE_LENGTH = int(getenv("TEXTS_MAX_VALUE_LENGTH", "1000"))
# Seconds a process trusts its copy of the catalogue before it asks the
# storefront whether the build changed (a conditional request, so an
# unchanged catalogue answers 304 with no body).
TEXTS_CATALOGUE_REVALIDATE_SECONDS = int(
    getenv("TEXTS_CATALOGUE_REVALIDATE_SECONDS", "60")
)
# Seconds to wait for the storefront when listing or validating texts.
TEXTS_STOREFRONT_TIMEOUT_SECONDS = float(
    getenv("TEXTS_STOREFRONT_TIMEOUT_SECONDS", "5")
)
# How long a store's overrides for one language are kept (they are also
# keyed by the store's cache generation, so a save moves them at once).
TEXTS_OVERRIDES_CACHE_SECONDS = int(
    getenv("TEXTS_OVERRIDES_CACHE_SECONDS", "3600")
)

# The storefront page builder (``builder``). Like the texts, the catalogue
# of blocks and their settings schemas is the storefront's, asked over
# ``NUXT_INTERNAL_BASE_URL`` (``builder.storefront``).
#
# Seconds a process trusts its copy of the catalogue before it asks the
# storefront whether the build changed (a conditional request).
BUILDER_CATALOGUE_REVALIDATE_SECONDS = int(
    getenv("BUILDER_CATALOGUE_REVALIDATE_SECONDS", "60")
)
# Seconds to wait for the storefront when fetching the catalogue.
BUILDER_STOREFRONT_TIMEOUT_SECONDS = float(
    getenv("BUILDER_STOREFRONT_TIMEOUT_SECONDS", "5")
)
# Limits a save is refused beyond. A block's own ``limits`` in the
# catalogue can only tighten them.
BUILDER_MAX_SECTIONS_PER_DOCUMENT = int(
    getenv("BUILDER_MAX_SECTIONS_PER_DOCUMENT", "40")
)
BUILDER_MAX_BLOCKS_PER_NODE = int(getenv("BUILDER_MAX_BLOCKS_PER_NODE", "50"))
# Ids a single ``builder/link-targets`` call resolves, per kind.
BUILDER_LINK_TARGETS_MAX_IDS = int(
    getenv("BUILDER_LINK_TARGETS_MAX_IDS", "100")
)
# Working copies one ``builder/preview/resolve`` call may carry.
BUILDER_PREVIEW_MAX_DOCUMENTS = int(
    getenv("BUILDER_PREVIEW_MAX_DOCUMENTS", "10")
)
# The editor's link picker (``builder/link-search``): results per kind, and
# the shortest query that searches (a shorter one answers an empty list).
BUILDER_LINK_SEARCH_LIMIT = int(getenv("BUILDER_LINK_SEARCH_LIMIT", "20"))
BUILDER_LINK_SEARCH_MIN_CHARS = int(
    getenv("BUILDER_LINK_SEARCH_MIN_CHARS", "2")
)
# Share-preview links (``builder.preview_links``): how long one opens, and
# how many may be open at once.
BUILDER_PREVIEW_LINK_TTL_SECONDS = int(
    getenv("BUILDER_PREVIEW_LINK_TTL_SECONDS", str(7 * 24 * 60 * 60))
)
BUILDER_MAX_PREVIEW_LINKS = int(getenv("BUILDER_MAX_PREVIEW_LINKS", "50"))
# Who else has a document open (``builder/presence``): how long a heartbeat
# keeps someone listed, and how many documents one heartbeat may name.
BUILDER_PRESENCE_TTL_SECONDS = int(getenv("BUILDER_PRESENCE_TTL_SECONDS", "45"))
BUILDER_PRESENCE_MAX_DOCUMENTS = int(
    getenv("BUILDER_PRESENCE_MAX_DOCUMENTS", "20")
)
# A contact attachment still PENDING after this many minutes had its scan
# lost (``contact.tasks.rescan_pending_attachments``); the sweep queues at
# most this many per store and run.
CONTACT_SCAN_PENDING_AFTER_MINUTES = int(
    getenv("CONTACT_SCAN_PENDING_AFTER_MINUTES", "15")
)
CONTACT_SCAN_SWEEP_BATCH = int(getenv("CONTACT_SCAN_SWEEP_BATCH", "200"))
# The review reminder (``order.tasks.send_review_request_emails``) runs
# daily: an order delivered up to this many days earlier than its due day
# is still reminded, so a run that did not happen costs nothing.
REVIEW_REQUEST_CATCH_UP_DAYS = int(getenv("REVIEW_REQUEST_CATCH_UP_DAYS", "3"))
# How long a guest order's uuid opens it (``Order.opens_for_guest``): long
# enough to pay, see the confirmation and follow a delivery. Store staff and a
# signed-in owner are not limited by it.
GUEST_ORDER_ACCESS_DAYS = int(getenv("GUEST_ORDER_ACCESS_DAYS", "14"))

# Levels of nesting: a section is level 1, its blocks level 2, theirs 3.
BUILDER_MAX_DEPTH = int(getenv("BUILDER_MAX_DEPTH", "3"))
BUILDER_MAX_DOCUMENT_BYTES = int(
    getenv("BUILDER_MAX_DOCUMENT_BYTES", str(256 * 1024))
)
BUILDER_MAX_PAGES = int(getenv("BUILDER_MAX_PAGES", "100"))
# Named saves kept per document that no release points at; older ones go.
BUILDER_UNRELEASED_CHECKPOINTS_KEPT = int(
    getenv("BUILDER_UNRELEASED_CHECKPOINTS_KEPT", "20")
)

# Most assets one ``builder/media/resolve`` call answers for.
BUILDER_MEDIA_RESOLVE_MAX_IDS = int(
    getenv("BUILDER_MEDIA_RESOLVE_MAX_IDS", "100")
)
# The staff ticket the store admin hands to the storefront editor, and the
# token the storefront trades it for (``builder.staff_session``). The ticket
# is single-use and rides a URL, so it lives a minute; the session is a
# working day and is never renewed by use.
BUILDER_STAFF_TICKET_TTL_SECONDS = int(
    getenv("BUILDER_STAFF_TICKET_TTL_SECONDS", "60")
)
BUILDER_STAFF_SESSION_TTL_SECONDS = int(
    getenv("BUILDER_STAFF_SESSION_TTL_SECONDS", str(8 * 60 * 60))
)
# Scheduled publishing: how far ahead a publish may be set, and how many may
# wait at once (``builder.scheduling``).
BUILDER_SCHEDULE_MAX_DAYS = int(getenv("BUILDER_SCHEDULE_MAX_DAYS", "365"))
BUILDER_MAX_PENDING_SCHEDULES = int(
    getenv("BUILDER_MAX_PENDING_SCHEDULES", "20")
)
# How long a resolved published page is kept. The key names the release,
# so a new release reads a new key and nothing is ever purged.
BUILDER_PUBLISHED_CACHE_SECONDS = int(
    getenv("BUILDER_PUBLISHED_CACHE_SECONDS", "86400")
)

# Minutes a provider refund attempt may stay PENDING before the provider is
# asked what became of it (``order.tasks.reconcile_pending_refunds``) and the
# store's dashboard lists it. Must stay under the 24 hours Stripe remembers an
# idempotency key for: until then a retry may reuse the attempt's key.
REFUND_RECONCILE_AFTER_MINUTES = int(
    getenv("REFUND_RECONCILE_AFTER_MINUTES", "60")
)
# Retention sweeps (``core.retention``): primary keys deleted per statement.
RETENTION_PURGE_BATCH_SIZE = int(getenv("RETENTION_PURGE_BATCH_SIZE", "1000"))
# Days a ``CachePurgeLog`` audit row (public schema, every store's purges)
# is kept.
CACHE_PURGE_LOG_RETENTION_DAYS = int(
    getenv("CACHE_PURGE_LOG_RETENTION_DAYS", "90")
)

# Tenant billing dunning (tenant/billing.py). WARN_DAYS before the
# term end the owner gets an expiry warning; GRACE_DAYS after it the
# store is suspended — but only once AUTO_SUSPEND is switched on
# (rollout default is notify-only: warnings and expired notices flow,
# nothing is suspended, and no email claims a suspension that did not
# happen). The platform admin's Plan & Billing page renders these live.
TENANT_BILLING = {
    "WARN_DAYS": int(getenv("TENANT_BILLING_WARN_DAYS", "14")),
    "GRACE_DAYS": int(getenv("TENANT_BILLING_GRACE_DAYS", "7")),
    "AUTO_SUSPEND": getenv("TENANT_BILLING_AUTO_SUSPEND", "False") == "True",
}

# Currency
DEFAULT_CURRENCY = getenv("DEFAULT_CURRENCY", "EUR")
CURRENCIES = ("EUR", "USD")
CURRENCY_CHOICES = [("EUR", "EUR €"), ("USD", "USD $")]

# Product favourites — per-user cap
MAX_FAVOURITES_PER_USER = int(getenv("MAX_FAVOURITES_PER_USER", "500"))

SEED_DEFAULT_COUNT = int(getenv("SEED_DEFAULT_COUNT", "10"))
SEED_BATCH_SIZE = int(getenv("SEED_BATCH_SIZE", "10"))

MEASUREMENT_BIDIMENSIONAL_SEPARATOR = "/"

# ---------------------------------------------------------------------------
# Tenant offboarding — retention of legally-mandated records
# ---------------------------------------------------------------------------
# When a tenant is destroyed, GDPR Art. 17/28(3)(g) requires its personal
# data to be erased — EXCEPT where Union or Member State law requires
# storage. Issued invoices are exactly that exception: the Greek Tax
# Procedure Code (Ν. 4987/2022, art. 13) requires accounting records to
# be kept for at least FIVE years from the END of the tax year in which
# the filing obligation arises. Because the return for tax year Y is
# filed in Y+1, five years from the end of that year lands on
# 31 December of Y+6 — hence the default below, expressed in years from
# the invoice's own tax year so the anchor is the legally correct one.
#
# Raise this where art. 36(2) extends the assessment period (unfiled
# returns, evasion indicators) — that can reach ten years. Lowering it
# below the statutory minimum would breach the retention duty; leaving
# records forever would breach storage limitation. Both directions are
# wrong, which is why this is an explicit, auditable knob rather than a
# constant buried in code.
TENANT_INVOICE_RETENTION_YEARS = int(
    getenv("TENANT_INVOICE_RETENTION_YEARS", "6")
)

# --------------------------------------------------------------------------
# Contact-attachment malware scanning (contact/scanners.py)
# --------------------------------------------------------------------------
# Which engine the CLUSTER runs, not what a store wants: a tenant cannot
# conjure a ClamAV deployment by ticking a box, so this is env, not an
# extra-setting. "null" records SKIPPED and relies on containment (a
# private tree with no URL, a staff-only streamed download, a magic-byte
# allow-list, and nothing on the server parsing the bytes) - which is the
# guarantee either way. "clamav" adds detection on top and needs a clamd
# reachable at CLAMAV_HOST; budget ~1.6 GB resident for its signatures
# and ~2.4 GB while it reloads them before enabling it.
# Per-tenant ceilings on attachment storage — see
# ``contact.attachments.storage_pressure``. The per-caller throttle is
# a REQUEST budget and bounds one visitor; these bound all of them at
# once, in BYTES, because the private tree is a shared volume that
# also holds invoice PDFs and filling it would take invoicing down as
# collateral.
#
# UNCLAIMED bounds abandoned uploads and is the alarm for a reaper
# that stopped: 512 MB fits a tenant's in-flight enquiries (20 x 25
# MB) comfortably.
#
# INTAKE bounds a FLOOD, over a rolling 24 hours and regardless of
# whether the uploads were claimed — claiming one exempts it from the
# reaper, so without this a caller who submits one enquiry per batch
# keeps every byte for the store's whole retention window. 1 GB/day is
# forty 25 MB tenders, which is a great deal more tenders than a
# quote-on-specification business receives in a day.
#
# Either at 0 disables that ceiling.
CONTACT_ATTACHMENTS_UNCLAIMED_BUDGET_MB = int(
    getenv("CONTACT_ATTACHMENTS_UNCLAIMED_BUDGET_MB", "512")
)
CONTACT_ATTACHMENTS_INTAKE_BUDGET_MB = int(
    getenv("CONTACT_ATTACHMENTS_INTAKE_BUDGET_MB", "1024")
)

CONTACT_ATTACHMENT_SCANNER = getenv("CONTACT_ATTACHMENT_SCANNER", "null")
CLAMAV_HOST = getenv("CLAMAV_HOST", "clamav")
CLAMAV_PORT = int(getenv("CLAMAV_PORT", "3310"))
CLAMAV_TIMEOUT = float(getenv("CLAMAV_TIMEOUT", "30"))

BLOG_COMMENT_AUTO_APPROVE = (
    getenv("BLOG_COMMENT_AUTO_APPROVE", "True") == "True"
)

# Related Posts Strategies Configuration. In order of closeness: the posts
# of the same category on the same topic (the words they share), then the rest
# of the category, then posts sharing a tag.
RELATED_POSTS_STRATEGIES = [
    {
        "strategy": "blog.strategies.shared_terms_related_posts_strategy.SharedTermsRelatedPostsStrategy",
        "weight": 1.0,
    },
    {
        "strategy": "blog.strategies.default_related_posts_strategy.DefaultRelatedPostsStrategy",
        "weight": 0.6,
    },
    {
        "strategy": "blog.strategies.tag_based_related_posts_strategy.TagBasedRelatedPostsStrategy",
        "weight": 0.4,
    },
]
RELATED_POSTS_LIMIT = 8
# How many of the store's newest published posts, of every category, the
# shared-words strategy reads: a word's rarity is measured over them, and
# the candidates are those of the post's category
# (docs/blog-related-posts.md).
RELATED_POSTS_CANDIDATES = int(getenv("RELATED_POSTS_CANDIDATES", "500"))

# The most posts the RSS feed endpoint (``/blog/post/feed``) returns.
BLOG_FEED_LIMIT = 50

# ---------- Recommendations ----------
# Attach attribution windows (docs/recommendations-engine.md §5). An
# order line "attaches" to an impression of the same product when the
# impression was shown to the SAME CART within this many hours (one
# shopping journey, guest or signed in) or to the SAME SIGNED-IN
# CUSTOMER within this many days (saw it on the phone, bought on the
# laptop). Both matches are recorded, labelled by which window caught
# them, so the two definitions can be compared on data.
RECOMMENDATION_ATTACH_CART_WINDOW_HOURS = int(
    getenv("RECOMMENDATION_ATTACH_CART_WINDOW_HOURS", "24")
)
RECOMMENDATION_ATTACH_USER_WINDOW_DAYS = int(
    getenv("RECOMMENDATION_ATTACH_USER_WINDOW_DAYS", "7")
)
# ``attributes``: a feature (attribute value, tag, brand) carried by more
# active products than this is not used to find candidates — it bounds
# the work on a large catalogue and says little about similarity there.
RECOMMENDATION_ATTRIBUTES_MAX_FEATURE_PRODUCTS = int(
    getenv("RECOMMENDATION_ATTRIBUTES_MAX_FEATURE_PRODUCTS", "500")
)
# ``co_purchase``: orders from this many days back count; the strategy
# declines until the store has this many orders with two or more lines;
# a pair bought together in fewer orders than the support is noise.
RECOMMENDATION_CO_PURCHASE_WINDOW_DAYS = int(
    getenv("RECOMMENDATION_CO_PURCHASE_WINDOW_DAYS", "180")
)
RECOMMENDATION_CO_PURCHASE_MIN_ORDERS = int(
    getenv("RECOMMENDATION_CO_PURCHASE_MIN_ORDERS", "50")
)
RECOMMENDATION_CO_PURCHASE_MIN_SUPPORT = int(
    getenv("RECOMMENDATION_CO_PURCHASE_MIN_SUPPORT", "5")
)
# Nightly weight learning (``recommendation/learning.py``): the trailing
# window of events read, the impressions a strategy needs on a surface
# before its weight moves, and the lowest weight learning may set.
RECOMMENDATION_LEARNING_WINDOW_DAYS = int(
    getenv("RECOMMENDATION_LEARNING_WINDOW_DAYS", "30")
)
RECOMMENDATION_LEARNING_MIN_IMPRESSIONS = int(
    getenv("RECOMMENDATION_LEARNING_MIN_IMPRESSIONS", "200")
)
RECOMMENDATION_LEARNING_WEIGHT_FLOOR = float(
    getenv("RECOMMENDATION_LEARNING_WEIGHT_FLOOR", "0.05")
)
# Days a ``RecommendationEvent`` is kept. It must outlive the longest
# window a reader looks back over (learning, attribution), or the engine
# would learn from a truncated history.
RECOMMENDATION_EVENT_RETENTION_DAYS = int(
    getenv("RECOMMENDATION_EVENT_RETENTION_DAYS", "90")
)
_RECOMMENDATION_LONGEST_WINDOW_DAYS = max(
    RECOMMENDATION_LEARNING_WINDOW_DAYS,
    RECOMMENDATION_ATTACH_USER_WINDOW_DAYS,
    -(-RECOMMENDATION_ATTACH_CART_WINDOW_HOURS // 24),
)
if RECOMMENDATION_EVENT_RETENTION_DAYS < _RECOMMENDATION_LONGEST_WINDOW_DAYS:
    raise ImproperlyConfigured(
        "RECOMMENDATION_EVENT_RETENTION_DAYS "
        f"({RECOMMENDATION_EVENT_RETENTION_DAYS}) is shorter than the "
        "longest window the recommendation engine reads events over "
        f"({_RECOMMENDATION_LONGEST_WINDOW_DAYS} days)."
    )

# Days a ``PendingPaymentFee`` waits for the payment it is for. Viva retries an
# unanswered webhook hourly for 24 hours, so a fee whose payment is still not
# booked after this long belongs to a payment that never will be (abandoned,
# failed): the row is deleted.
PENDING_PAYMENT_FEE_RETENTION_DAYS = int(
    getenv("PENDING_PAYMENT_FEE_RETENTION_DAYS", "90")
)

# ---------- Store-admin tools over MCP (agent/staff.py) --------------------
# The host a store's staff connect an AI app on: the staff issuer of the
# public schema, served alone through ``agent.urls_connect``
# (``tenant.middleware.PlatformTenantMainMiddleware``) and outside the
# platform host's basic-auth. Empty turns the host off.
STAFF_CONNECT_HOST = getenv("STAFF_CONNECT_HOST", "")
# The most rows one list on ``/api/v1/agent/staff/*`` returns.
STAFF_AGENT_LIST_LIMIT = int(getenv("STAFF_AGENT_LIST_LIMIT", "50"))
# The days a sales or profit report covers when no start is given.
STAFF_AGENT_REPORT_DEFAULT_DAYS = int(
    getenv("STAFF_AGENT_REPORT_DEFAULT_DAYS", "30")
)
