"""``Tenant``: a store, the control plane's row, and its queryset."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.expressions import Combinable
from django.utils.translation import gettext_lazy as _
from django_tenants.models import TenantMixin, _check_schema_name
from django_tenants.utils import get_public_schema_name
from simple_history.models import HistoricalRecords

from core.db.encrypted_fields import EncryptedCharField
from core.fields.url import WebURLField
from core.json_schema import JSONSchemaValidator
from core.models import TimeStampMixinModel, UUIDModel
from core.utils.tenant_urls import (
    STOREFRONT_DEFAULT_LOCALE,
    storefront_language_choices,
)
from tenant.enum import (
    StoreVertical,
    SuspendedReason,
    TenantPlan,
)
from tenant.models.tenant_rules import (
    validate_available_locales,
    validate_box_now_partner_id,
    validate_clarity_project_id,
    validate_cloudflare_credentials,
    validate_ga_tracking_id,
    validate_google_ads,
    validate_legal_documents_for_new_locales,
    validate_meta_pixel_id,
    validate_openai_pixel_id,
    validate_resend_credentials,
    validate_schema_name,
    validate_social_urls,
    validate_store_description_i18n,
    validate_stripe_publishable_key,
    validate_stripe_secret_key,
    validate_tiktok_pixel_id,
)
from tenant.validators import (
    INDEXNOW_KEY_PATTERN,
    SocialProfileUrlValidator,
    generate_indexnow_key,
    validate_reserved_schema_name,
)


class TenantQuerySet(models.QuerySet):
    """Which stores a piece of work reaches, read from the lifecycle's state.

    ``tenant.lifecycle`` keeps a store row in one of two states: active, or
    suspended (``is_active`` false, ``suspended_at`` stamped). Destroying a
    store drops its schema and deletes its row in one transaction
    (``destroy_tenant``), so there is no row left behind for a store being
    removed, and every row these return is a store whose schema exists.
    """

    #: ``Tenant.is_suspended`` is the same rule read off one row.
    _ACTIVE = models.Q(is_active=True, suspended_at__isnull=True)

    def stores(self) -> TenantQuerySet:
        """Every store, active or suspended: each row but the platform's."""
        return self.exclude(schema_name=get_public_schema_name())

    def active(self) -> TenantQuerySet:
        """The stores that are neither suspended nor switched off."""
        return self.stores().filter(self._ACTIVE)

    def unavailable(self) -> TenantQuerySet:
        """The stores ``active`` leaves out: suspended or switched off."""
        return self.stores().exclude(self._ACTIVE)


class Tenant(TenantMixin, TimeStampMixinModel, UUIDModel):
    # Redeclares ``TenantMixin.schema_name`` (abstract base — safe to
    # override) to layer ``validate_reserved_schema_name`` on top of
    # django-tenants' own PostgreSQL-identifier format check
    # (``_check_schema_name``). Field shape (max_length, unique,
    # db_index) mirrors the base field exactly; only the validators
    # list changes.
    schema_name = models.CharField(
        _("Schema name"),
        max_length=63,
        unique=True,
        db_index=True,
        validators=[_check_schema_name, validate_reserved_schema_name],
    )

    name = models.CharField(_("Name"), max_length=100)
    slug = models.SlugField(_("Slug"), max_length=100, unique=True)
    owner_email = models.EmailField(_("Owner Email"))
    is_active = models.BooleanField(_("Active"), default=True)
    suspended_at = models.DateTimeField(
        _("Suspended At"),
        null=True,
        blank=True,
        help_text=_(
            "Timestamp when this tenant was last suspended. "
            "Set automatically by the suspend admin action. "
            "Required to be at least 24 hours in the past before "
            "the tenant can be permanently destroyed."
        ),
    )

    suspended_reason = models.CharField(
        _("Suspended Reason"),
        max_length=20,
        choices=SuspendedReason.choices,
        blank=True,
        default="",
        help_text=_(
            "Why this tenant is suspended: 'manual' for operator "
            "action, 'billing' for the automated dunning task. Empty "
            "when not suspended. Renewal-driven auto-reactivation "
            "applies to 'billing' suspensions only."
        ),
    )

    # Plan / billing
    plan = models.CharField(
        _("Plan"),
        max_length=20,
        choices=TenantPlan.choices,
        default=TenantPlan.TRIAL,
    )
    paid_until = models.DateField(
        _("Paid Until"),
        null=True,
        blank=True,
        help_text=_(
            "Last day (inclusive) of the current term — trial or paid. "
            "Empty means the term never expires (legacy rows, the "
            "platform row). The daily billing task warns, dunns, and "
            "optionally suspends from this date."
        ),
    )

    # Dunning bookkeeping — written only by the billing task. The pair
    # makes the pipeline idempotent AND self-resetting: a stage is only
    # valid for the term it was recorded against, so moving paid_until
    # forward implicitly resets the dunning state with no signal.
    billing_notice_stage = models.PositiveSmallIntegerField(
        _("Billing Notice Stage"),
        default=0,
        help_text=_(
            "Highest dunning stage already notified for the current "
            "term (0 none, 1 expiry warning, 2 expired, 3 suspended)."
        ),
    )
    billing_notice_term = models.DateField(
        _("Billing Notice Term"),
        null=True,
        blank=True,
        help_text=_(
            "The paid_until value the notice stage refers to. A "
            "mismatch with the current paid_until means the stage is "
            "stale and resets to 0 on the next billing run."
        ),
    )

    # Branding
    store_name = models.CharField(
        _("Store Name"), max_length=200, blank=True, default=""
    )
    store_description = models.TextField(
        _("Store Description"), blank=True, default=""
    )
    store_description_i18n = models.JSONField(
        _("Store Description (other languages)"),
        default=dict,
        db_default={},
        blank=True,
        validators=[
            JSONSchemaValidator("tenant.json_schemas.store_description_i18n")
        ],
        help_text=_(
            "The store line in the storefront's other languages, keyed "
            'by locale, e.g. {"en": "..."}. The default locale line is '
            "Store Description; a locale left out falls back to it."
        ),
    )
    default_locale = models.CharField(
        _("Default Locale"),
        max_length=10,
        choices=storefront_language_choices,
        default=STOREFRONT_DEFAULT_LOCALE,
    )
    default_currency = models.CharField(
        _("Default Currency"),
        max_length=3,
        # Every money path works in DEFAULT_CURRENCY and nothing converts
        # (``tenant.role_scopes`` says why a store may not pick another),
        # so offering a second currency only lets a store render prices
        # the backend never charged. Add one together with the code that
        # honours it.
        choices=[(settings.DEFAULT_CURRENCY, settings.DEFAULT_CURRENCY)],
        default=settings.DEFAULT_CURRENCY,
    )
    available_locales = models.JSONField(
        _("Available Locales"),
        default=list,
        blank=True,
        validators=[
            JSONSchemaValidator("tenant.json_schemas.available_locales")
        ],
        help_text=_(
            'Locales this store serves, e.g. ["el", "en"]. Empty '
            "means single-language on the default locale — the "
            "storefront then 404s every other locale prefix and hides "
            "it from the language switcher, hreflang set and sitemap. "
            "Must contain default_locale when non-empty."
        ),
    )

    # Assets
    logo_light_url = WebURLField(_("Logo (Light)"), blank=True, default="")
    logo_dark_url = WebURLField(_("Logo (Dark)"), blank=True, default="")
    favicon_url = WebURLField(_("Favicon URL"), blank=True, default="")

    # The platform's own storefront. At most one store carries this: it
    # renders the brand assets bundled in the storefront image (navbar
    # wordmark, favicon set, OG card, login mark) and the platform-only
    # discovery surfaces (llms.txt); every other store renders exactly
    # what its own rows carry. A ROW FLAG on purpose, never a hostname in
    # env: the storefront used to recognise this tenant by comparing its
    # primary domain with ``NUXT_PUBLIC_BASE_URL``, which put the first
    # store's hostname into every tenant's serialized runtime config and
    # Django's twin read ``NUXT_BASE_URL`` for the same comparison.
    # ``db_default`` for the same reason as ``is_protected`` below.
    is_platform_storefront = models.BooleanField(
        _("Platform storefront"),
        default=False,
        db_default=False,
        help_text=_(
            "This store is the platform's own storefront: it renders the "
            "brand assets bundled in the storefront image. At most one "
            "tenant may carry this."
        ),
    )

    # SEO attribution and site verification — per-store data, emitted
    # only on the store that set it. These were platform env values
    # (tenant #1's) until 2026-09-10, so every store's pages carried the
    # first store's author and verification tokens in their payload.
    seo_author = models.CharField(
        _("SEO author"),
        max_length=120,
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "Rendered as the storefront's author/creator/publisher meta "
            "tags. Empty → the tags are not emitted."
        ),
    )
    google_site_verification = models.CharField(
        _("Google site verification"),
        max_length=128,
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "Google Search Console verification token (the content of "
            "the google-site-verification meta tag). Empty → not emitted."
        ),
    )
    bing_site_verification = models.CharField(
        _("Bing site verification"),
        max_length=128,
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "Bing Webmaster Tools verification token (the content of "
            "the msvalidate.01 meta tag). Empty → not emitted."
        ),
    )
    pinterest_domain_verify = models.CharField(
        _("Pinterest domain verification"),
        max_length=128,
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "Pinterest domain verification token (the content of the "
            "p:domain_verify meta tag). Empty → not emitted."
        ),
    )
    indexnow_key = models.CharField(
        _("IndexNow key"),
        max_length=128,
        default=generate_indexnow_key,
        db_default="",
        validators=[RegexValidator(INDEXNOW_KEY_PATTERN)],
        help_text=_(
            "The key the store proves its ownership to search engines "
            "with when it notifies them through IndexNow. The storefront "
            "serves it at /<key>.txt while the store's INDEXNOW_ENABLED "
            "setting is on. Public by design."
        ),
    )

    # Marks a tenant as a throwaway showcase rather than a real
    # merchant. It is NOT a feature flag and gates nothing a shopper
    # sees — the demo store is meant to look exactly like a real one.
    #
    # It exists because ``manage.py seed_demo_store`` refuses any tenant
    # whose domains lack a non-production marker, and the public demo
    # store lives on ``demo.grooveshop.space``, a production host with
    # no such marker. The alternatives were both worse: ``--force`` is
    # the blanket override that also unlocks every live store, and
    # re-adding a hostname substring is what let the guard classify
    # tenant #2's live ``fyteia.grooveshop.space`` as safe to seed over.
    # An explicit per-tenant opt-in cannot be got wrong by accident.
    #
    # Deliberately NOT surfaced on TenantConfigSerializer: the storefront
    # has no use for it, and every field added there has to outlive the
    # cached payloads of the deploy that adds it (2026-08-31 outage).
    is_demo = models.BooleanField(
        _("Demo Store"),
        default=False,
        help_text=_(
            "This tenant is a disposable showcase, not a real "
            "merchant. Allows manage.py seed_demo_store to overwrite "
            "its catalogue and showcase content without --force. "
            "Never set this on a store that takes real orders."
        ),
    )

    # Protection is a row attribute rather than a list of schema names
    # in code: the platform's own store and any merchant the operators
    # choose are shielded from suspend/activate/destroy without a deploy,
    # and no customer is named in the codebase. The public schema is
    # protected by construction (tenant.lifecycle.is_protected_tenant).
    # ``db_default`` as well as ``default``: Django drops the column
    # default right after ADD COLUMN unless one is declared, and the
    # PreSync hook migrates BEFORE the new image rolls — old pods still
    # inserting Tenant history rows without this column would fail for
    # the length of the rollout (and after any image rollback).
    is_protected = models.BooleanField(
        _("Protected"),
        default=False,
        db_default=False,
        help_text=_(
            "Never suspend, reactivate or destroy this tenant through "
            "admin actions, the platform API or automation."
        ),
    )
    # Part of every key the tenant's cached resolve payload and domain
    # set are read under (``tenant.cache``), and bumped in the same
    # statement or transaction as each write that changes them. A
    # committed write moves every reader to a new key, so no Redis
    # DELETE has to land for a stale entry to stop being served — and
    # the fail-open cache (``src/core/cache_backend.py``) drops deletes during an
    # outage. Only ever written relatively, as ``F() + 1`` (``save``
    # below, ``tenant.cache.bump_cache_generation``), so a stale
    # in-memory copy can never write an older generation back — and the
    # annotation admits the ``F()`` that ``save`` assigns.
    cache_generation: models.PositiveBigIntegerField[int | Combinable, int] = (
        models.PositiveBigIntegerField(
            _("Cache Generation"),
            default=0,
            db_default=0,
            editable=False,
        )
    )

    # Feature flags
    loyalty_enabled = models.BooleanField(_("Loyalty Enabled"), default=False)
    blog_enabled = models.BooleanField(_("Blog Enabled"), default=True)
    promotions_enabled = models.BooleanField(
        _("Promotions Enabled"), default=False
    )
    gift_cards_enabled = models.BooleanField(
        _("Gift Cards Enabled"), default=False
    )
    # Gates the ENTIRE agent-gateway surface for this tenant: MCP
    # commerce tools, UCP/ACP agentic checkout, catalog feeds and the
    # storefront chatbot backend. Default True — the surface predates
    # the flag and is live for existing tenants; the flag exists so the
    # platform can opt a tenant OUT (the merchant runtime tier is the
    # AGENT_COMMERCE_ENABLED extra-setting, folded into TenantConfig).
    agent_commerce_enabled = models.BooleanField(
        _("Agent Commerce Enabled"), default=True
    )
    # Plan gate for the wholesale program (business profiles, customer
    # groups, price lists). The merchant runtime tier is the
    # B2B_WHOLESALE_ENABLED extra-setting; both must hold. Invoice-at-
    # checkout is NOT behind this — that stays a plain merchant setting
    # (B2B_INVOICING_ENABLED) available on every plan.
    b2b_enabled = models.BooleanField(_("B2B Enabled"), default=False)
    # Plan gate for product suggestions (docs/recommendations-engine.md).
    # The merchant runtime tier is the PRODUCT_SUGGESTIONS_ENABLED
    # extra-setting; both must hold. Which STRATEGIES run is a further,
    # finer gate on ``plan`` itself — see ``RecommendationStrategy
    # .min_plan`` — so this flag is "the feature exists for this store"
    # and the plan rank is "how smart it is allowed to be".
    recommendations_enabled = models.BooleanField(
        _("Recommendations Enabled"), default=False
    )
    # Which vertical preset seeds this store's recommendation slots at
    # provisioning and on every deploy (``backfill_recommendation_slots``
    # in the PreSync job), and what "Reset to preset" in the slot admin
    # resets to. Platform-set; a merchant edits the resulting slots.
    vertical = models.CharField(
        _("Store vertical"),
        max_length=20,
        choices=StoreVertical.choices,
        default=StoreVertical.GENERAL,
        help_text=_(
            "What the store sells. Selects the recommendation preset the "
            "store starts from; merchants can still edit every slot."
        ),
    )

    # Stripe Connect — dormant/reserved. The platform runs SEPARATE
    # Stripe accounts per tenant (``stripe_secret_key`` below), not
    # Connect: dj-stripe's webhook pipeline has no per-connected-account
    # event routing, while its per-schema WebhookEndpoint/APIKey design
    # maps 1:1 onto one-account-per-schema. Kept as a future option.
    stripe_connect_account_id = models.CharField(
        _("Stripe Connect Account ID"),
        max_length=255,
        blank=True,
        default="",
    )

    stripe_secret_key = EncryptedCharField(
        _("Stripe secret key"),
        max_length=255,
        db_column="stripe_secret_key_encrypted",
        blank=True,
        default="",
        help_text=_(
            "The tenant's OWN Stripe secret key (sk_live_*, sk_test_* "
            "or a restricted rk_* key). Secret — never expose to the "
            "browser. Empty means Stripe is unavailable for this "
            "tenant — there is no platform-wide fallback."
        ),
    )

    # Public Stripe key — safe to expose to unauthenticated callers.
    # Use the per-tenant ``pk_live_*`` / ``pk_test_*`` key here.
    # Empty string means Stripe is unconfigured for this tenant — there
    # is no platform-wide fallback (mirrors stripe_secret_key).
    stripe_publishable_key = models.CharField(
        _("Stripe publishable key"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "Per-tenant Stripe publishable key (pk_live_* or pk_test_*). "
            "This value is public by design and is safe to expose to "
            "unauthenticated callers. Leave blank if Stripe is not "
            "configured for this tenant."
        ),
    )

    # Extra CSP origins for the storefront.
    allowed_csp_sources = models.JSONField(
        _("Allowed CSP sources"),
        default=list,
        blank=True,
        validators=[
            JSONSchemaValidator("tenant.json_schemas.allowed_csp_sources")
        ],
        help_text=_(
            "Additional origins allowed by the storefront CSP "
            "(connect-src, img-src, script-src, frame-src). "
            "Each entry must start with https://, http://localhost, or wss://."
        ),
    )

    # === Edge cache (Cloudflare) ===
    # Only for a store on its OWN domain proxied through its OWN Cloudflare
    # account. Stores on a platform hostname are purged with the
    # platform's credentials (core.cache.edge). Set both or neither.

    cloudflare_zone_id = models.CharField(
        _("Cloudflare zone ID"),
        max_length=32,
        blank=True,
        default="",
        db_default="",
        validators=[
            RegexValidator(
                r"^[0-9a-f]{32}$",
                _("A Cloudflare zone ID is 32 lowercase hex characters."),
            )
        ],
        help_text=_(
            "The zone of the store's own domain, from the Cloudflare "
            "dashboard's domain overview. Empty when the store uses a "
            "platform hostname."
        ),
    )
    cloudflare_api_token = EncryptedCharField(
        _("Cloudflare API token"),
        max_length=255,
        db_column="cloudflare_api_token_encrypted",
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "A token with only Zone → Cache Purge → Purge on that zone. "
            "Secret — used to clear the store's pages from Cloudflare's "
            "cache after an edit or a deploy."
        ),
    )

    # === Analytics ===

    meta_pixel_id = models.CharField(
        _("Meta Pixel ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Meta (Facebook) Pixel ID for the storefront. "
            "Digits only (e.g. '123456789012345'). "
            "Empty → browser pixel is disabled for this tenant."
        ),
    )
    tiktok_pixel_id = models.CharField(
        _("TikTok Pixel ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "TikTok Pixel ID for the storefront (e.g. 'C0ABCDEFGH123'). "
            "Alphanumeric only. "
            "Empty → TikTok pixel is disabled for this tenant."
        ),
    )
    clarity_project_id = models.CharField(
        _("Microsoft Clarity project ID"),
        max_length=64,
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "Microsoft Clarity project ID for the storefront (e.g. "
            "'a1b2c3d4e5'), under Settings > Overview in Clarity. "
            "Alphanumeric only. Empty → Clarity is disabled for this tenant."
        ),
    )
    openai_pixel_id = models.CharField(
        _("ChatGPT Ads Pixel ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "OpenAI / ChatGPT Ads conversion pixel ID (e.g. "
            "'8MktrqpXN1MRdD2NUfkXmU'). Alphanumeric only. "
            "Empty → the ChatGPT Ads pixel is disabled for this tenant, "
            "and its origin is left out of the CSP."
        ),
    )

    ga_tracking_id = models.CharField(
        _("Google Analytics Tracking ID"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "Google Analytics 4 measurement ID (G-XXXXXXXXXX) or "
            "Universal Analytics property (UA-XXXXXXXX-X). "
            "Empty → GA is disabled for this tenant."
        ),
    )
    # Google Ads conversion tracking. One conversion ID per store and one
    # LABEL per conversion action the storefront can report. The labels
    # are explicit columns rather than a JSON map on purpose: the set of
    # actions is closed — it is exactly the events the storefront fires
    # — so a new action is a code change with a call site, and a column
    # beside it is the honest cost. It also keeps them typed end to end
    # (OpenAPI → Zod → TenantConfig) and editable as plain admin fields.
    google_ads_conversion_id = models.CharField(
        _("Google Ads Conversion ID"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "Google Ads conversion ID (AW-XXXXXXXXXX). Empty → no "
            "conversion events are sent for this tenant, whatever the "
            "labels below say."
        ),
    )
    google_ads_purchase_label = models.CharField(
        _("Google Ads Purchase Label"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Conversion label for a completed purchase — the part after "
            "the slash in 'AW-XXXXXXXXXX/LABEL'. Sent with the order "
            "total, currency and transaction id."
        ),
    )
    google_ads_add_to_cart_label = models.CharField(
        _("Google Ads Add-to-Cart Label"),
        max_length=64,
        blank=True,
        default="",
        help_text=_("Conversion label for adding a product to the cart."),
    )
    google_ads_begin_checkout_label = models.CharField(
        _("Google Ads Begin-Checkout Label"),
        max_length=64,
        blank=True,
        default="",
        help_text=_("Conversion label for starting the checkout."),
    )
    google_ads_add_payment_info_label = models.CharField(
        _("Google Ads Add-Payment-Info Label"),
        max_length=64,
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "Conversion label for the shopper submitting payment details "
            "at checkout. Sent with the cart value and currency."
        ),
    )
    google_ads_page_view_label = models.CharField(
        _("Google Ads Page-View Label"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Conversion label for a page view. Sent on every navigation, "
            "so leave it empty unless a campaign is built on it."
        ),
    )

    # === Authentication ===

    totp_issuer = models.CharField(
        _("TOTP Issuer"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Issuer name shown in authenticator apps when the user "
            "scans the TOTP QR code (e.g. 'MyShop'). "
            "Empty uses the store name."
        ),
    )

    # === Social Links ===

    socials_discord = WebURLField(
        _("Discord URL"),
        blank=True,
        default="",
        validators=[SocialProfileUrlValidator(("discord.gg", "discord.com"))],
        help_text=_(
            "Discord server invite URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_facebook = WebURLField(
        _("Facebook URL"),
        blank=True,
        default="",
        validators=[SocialProfileUrlValidator(("facebook.com", "fb.com"))],
        help_text=_(
            "Facebook page URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_instagram = WebURLField(
        _("Instagram URL"),
        blank=True,
        default="",
        validators=[SocialProfileUrlValidator(("instagram.com",))],
        help_text=_(
            "Instagram profile URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_pinterest = WebURLField(
        _("Pinterest URL"),
        blank=True,
        default="",
        validators=[
            SocialProfileUrlValidator(
                ("pinterest.com",), country_subdomains=True
            )
        ],
        help_text=_(
            "Pinterest profile URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_reddit = WebURLField(
        _("Reddit URL"),
        blank=True,
        default="",
        validators=[SocialProfileUrlValidator(("reddit.com",))],
        help_text=_(
            "Reddit community/profile URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_tiktok = WebURLField(
        _("TikTok URL"),
        blank=True,
        default="",
        validators=[SocialProfileUrlValidator(("tiktok.com",))],
        help_text=_(
            "TikTok profile URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    # LinkedIn was the conspicuous omission in a set of eight consumer
    # networks: it is the ONLY social presence a B2B engineering
    # practice keeps.
    socials_linkedin = WebURLField(
        _("LinkedIn URL"),
        blank=True,
        default="",
        validators=[
            SocialProfileUrlValidator(
                ("linkedin.com",), country_subdomains=True
            )
        ],
        help_text=_(
            "LinkedIn company page URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_twitter = WebURLField(
        _("Twitter / X URL"),
        blank=True,
        default="",
        validators=[SocialProfileUrlValidator(("twitter.com", "x.com"))],
        help_text=_(
            "Twitter/X profile URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )
    socials_youtube = WebURLField(
        _("YouTube URL"),
        blank=True,
        default="",
        validators=[SocialProfileUrlValidator(("youtube.com",))],
        help_text=_(
            "YouTube channel URL. Must use https://. "
            "Empty → link hidden in storefront footer."
        ),
    )

    # === Shipping — BoxNow (public field) ===

    box_now_partner_id = models.CharField(
        _("BoxNow Partner ID"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "BoxNow partner identifier (digits only). "
            "Used by the storefront BoxNow widget. "
            "No platform fallback — empty disables BoxNow."
        ),
    )

    # -----------------------------------------------------------------------
    # Admin-only / server-only fields — NOT exposed via TenantConfigSerializer
    # -----------------------------------------------------------------------

    # === Email ===

    from_email = models.EmailField(
        _("From Email"),
        blank=True,
        default="",
        help_text=_(
            "Your own sender address, e.g. orders@your-domain.gr. It "
            "takes effect only once the platform ticks From Email "
            "Verified, which happens after your domain is authenticated "
            "on the mail relay. Until then mail goes out from the "
            "platform address with your store name as the display name. "
            "Set Contact Email too: that is the reply-to address "
            "customers actually reach you on."
        ),
    )
    from_email_verified = models.BooleanField(
        _("From Email verified"),
        default=False,
        help_text=_(
            "Tick ONLY once this store's sending domain is authenticated "
            "on the platform mail relay — SPF and DKIM published and "
            "confirmed by the relay. Until then From Email is ignored "
            "and the platform address is used, because that is the only "
            "domain the relay can sign for. Ticking this early makes "
            "every message from this store fail DMARC and land in spam."
        ),
    )
    resend_api_key = EncryptedCharField(
        _("Resend API key"),
        max_length=255,
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "The sending key of this store's own Resend team, restricted "
            "to its verified domain. Secret. Used only while From Email "
            "is verified; a store without its own domain sends through "
            "the platform's team. There is NO platform fallback for a "
            "verified store: empty means its mail is not sent."
        ),
    )
    resend_webhook_secret = EncryptedCharField(
        _("Resend webhook signing secret"),
        max_length=255,
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "The signing secret of the webhook endpoint in this store's "
            "own Resend team, used to verify the delivery events it "
            "sends. Secret. Empty for a store on the platform's team."
        ),
    )
    contact_email = models.EmailField(
        _("Contact Email"),
        blank=True,
        default="",
        help_text=_(
            "Public contact email shown in storefront footer and "
            "contact page, and the inbox for contact-form submissions. "
            "Empty means the store has no contact address: submissions "
            "are not delivered and outbound mail carries no Reply-To."
        ),
    )

    # === Analytics (server-side) ===

    meta_capi_access_token = EncryptedCharField(
        _("Meta CAPI Access Token"),
        max_length=255,
        db_column="meta_capi_access_token_encrypted",
        blank=True,
        default="",
        help_text=_(
            "Meta Conversions API system user access token. "
            "Secret — never expose to the browser. "
            "No platform fallback — empty disables Meta CAPI."
        ),
    )

    # === Payments — Viva Wallet ===

    viva_wallet_merchant_id = models.CharField(
        _("Viva Wallet Merchant ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "Viva Wallet merchant ID. "
            "No platform fallback — empty disables Viva Wallet."
        ),
    )
    viva_wallet_api_key = EncryptedCharField(
        _("Viva Wallet API Key"),
        max_length=255,
        db_column="viva_wallet_api_key_encrypted",
        blank=True,
        default="",
        help_text=_(
            "Viva Wallet API key (classic auth). "
            "No platform fallback — empty disables Viva Wallet."
        ),
    )
    viva_wallet_client_id = models.CharField(
        _("Viva Wallet Client ID"),
        max_length=255,
        blank=True,
        default="",
        help_text=_(
            "Viva Wallet OAuth2 client ID. "
            "No platform fallback — empty disables Viva Wallet."
        ),
    )
    viva_wallet_client_secret = EncryptedCharField(
        _("Viva Wallet Client Secret"),
        max_length=255,
        db_column="viva_wallet_client_secret_encrypted",
        blank=True,
        default="",
        help_text=_(
            "Viva Wallet OAuth2 client secret. "
            "No platform fallback — empty disables Viva Wallet."
        ),
    )
    viva_wallet_webhook_verification_key = EncryptedCharField(
        _("Viva Wallet Webhook Verification Key"),
        max_length=255,
        db_column="viva_wallet_webhook_verification_key_encrypted",
        blank=True,
        default="",
        help_text=_(
            "Viva Wallet webhook verification key for HMAC validation. "
            "No platform fallback — empty disables Viva Wallet."
        ),
    )
    viva_wallet_source_code = models.CharField(
        _("Viva Wallet Source Code"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "Viva Wallet Smart Checkout payment source code — a "
            "per-merchant identifier, so each tenant needs their own. "
            "No platform fallback — empty disables Viva Wallet."
        ),
    )
    viva_wallet_live_mode = models.BooleanField(
        _("Viva Wallet Live Mode"),
        null=True,
        blank=True,
        default=None,
        help_text=_(
            "Whether this tenant's Viva credentials are live (demo "
            "otherwise). There is NO settings fallback: unset is read as "
            "demo, so a live merchant MUST set this explicitly or real "
            "payments are sent to the Viva demo environment."
        ),
    )

    # === Shipping — ACS ===

    acs_api_key = EncryptedCharField(
        _("ACS API Key"),
        max_length=255,
        db_column="acs_api_key_encrypted",
        blank=True,
        default="",
        help_text=_(
            "ACS courier API key. No platform fallback — empty disables ACS."
        ),
    )
    acs_company_id = models.CharField(
        _("ACS Company ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "ACS company identifier. No platform fallback — empty disables ACS."
        ),
    )
    acs_company_password = EncryptedCharField(
        _("ACS Company Password"),
        max_length=128,
        db_column="acs_company_password_encrypted",
        blank=True,
        default="",
        help_text=_(
            "ACS company password. No platform fallback — empty disables ACS."
        ),
    )
    acs_user_id = models.CharField(
        _("ACS User ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "ACS API user identifier. No platform fallback — empty disables ACS."
        ),
    )
    acs_user_password = EncryptedCharField(
        _("ACS User Password"),
        max_length=128,
        db_column="acs_user_password_encrypted",
        blank=True,
        default="",
        help_text=_(
            "ACS API user password. No platform fallback — empty disables ACS."
        ),
    )
    acs_billing_code = models.CharField(
        _("ACS Billing Code"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "ACS billing code (e.g. 'ΑΚ12345678'). "
            "No platform fallback — empty disables ACS."
        ),
    )
    acs_station_origin = models.CharField(
        _("ACS Station Origin"),
        max_length=8,
        blank=True,
        default="",
        help_text=_(
            "ACS origin station code (up to 8 chars), used to quote "
            "shipping. Optional: when empty it is taken from the "
            "billing code. No platform fallback."
        ),
    )

    # === Shipping — BoxNow (secrets) ===

    box_now_client_id = models.CharField(
        _("BoxNow Client ID"),
        max_length=64,
        blank=True,
        default="",
        help_text=_(
            "BoxNow OAuth2 client ID. "
            "No platform fallback — empty disables BoxNow."
        ),
    )
    box_now_client_secret = EncryptedCharField(
        _("BoxNow Client Secret"),
        max_length=255,
        db_column="box_now_client_secret_encrypted",
        blank=True,
        default="",
        help_text=_(
            "BoxNow OAuth2 client secret. "
            "No platform fallback — empty disables BoxNow."
        ),
    )
    box_now_warehouse_id = models.CharField(
        _("BoxNow Warehouse ID"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "BoxNow warehouse identifier. "
            "No platform fallback — empty disables BoxNow."
        ),
    )
    box_now_notify_phone = models.CharField(
        _("BoxNow Notify Phone"),
        max_length=32,
        blank=True,
        default="",
        help_text=_(
            "Phone number for BoxNow shipment notifications, sent as the "
            "sender's contact number. Optional: left out of the request "
            "when empty. No platform fallback."
        ),
    )
    box_now_live_mode = models.BooleanField(
        _("BoxNow Live Mode"),
        default=False,
        db_default=False,
        help_text=_(
            "Which BoxNow environment this tenant's keys belong to: "
            "production when on, stage when off. BoxNow stage and "
            "production keys only work against their own API, so set this "
            "to match the keys entered here. There is NO platform "
            "fallback: off means stage."
        ),
    )
    box_now_webhook_secret = EncryptedCharField(
        _("BoxNow Webhook Secret"),
        max_length=255,
        db_column="box_now_webhook_secret_encrypted",
        blank=True,
        default="",
        help_text=_(
            "HMAC secret for verifying BoxNow webhook deliveries — "
            "per-tenant, since each tenant holds their own BoxNow "
            "contract. No platform fallback — empty disables BoxNow."
        ),
    )

    # === Invoicing — myDATA (AADE) ===
    # Per-tenant like every other merchant credential: each store invoices
    # under its own TAXISnet account. ``MYDATA_ENABLED`` and the rest of
    # the myDATA knobs stay ``extra_settings`` rows.

    mydata_user_id = models.CharField(
        _("myDATA user ID"),
        max_length=64,
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "The aade-user-id of the store's TAXISnet myDATA developer "
            "account. No platform fallback — empty disables myDATA."
        ),
    )
    mydata_subscription_key = EncryptedCharField(
        _("myDATA subscription key"),
        max_length=255,
        blank=True,
        default="",
        db_default="",
        help_text=_(
            "The Ocp-Apim-Subscription-Key from the myDATA developer "
            "portal. Secret. No platform fallback — empty disables "
            "myDATA."
        ),
    )

    # === Agentic Commerce (secret) ===

    acp_bearer_token = EncryptedCharField(
        _("ACP Bearer Token"),
        max_length=255,
        db_column="acp_bearer_token_encrypted",
        blank=True,
        default="",
        help_text=_(
            "Bearer token the agent platform uses on this tenant's "
            "/acp/* checkout surface (served to the agent gateway only "
            "via the internal-token-gated tenant resolve endpoint). "
            "Per-tenant — a token minted for one store must never "
            "drive another store's checkout. Empty disables ACP for "
            "this tenant."
        ),
    )
    # Platform tier of the hosted-payment selection gate. The merchant
    # tier is the AGENT_HOSTED_PAYMENT_ENABLED extra-setting, and
    # TenantConfig serves the AND of the two.
    #
    # Default ON because it only exposes a choice the store already
    # offers on the web: an agent may name an ONLINE pay-way so the
    # order is placed and the buyer authorises on the PSP's page,
    # instead of being sent back to re-enter the whole checkout. UCP
    # cannot model that selection itself — an online method is not an
    # instrument a platform can acquire — so it travels as a declared
    # extension, and this flag is how a platform withdraws it.
    agent_hosted_payment_enabled = models.BooleanField(
        _("Agent Hosted Payment Selection"),
        default=True,
        help_text=_(
            "Let AI-agent platforms choose an online payment method, "
            "placing the order and handing the buyer the payment page. "
            "When off, agents can only complete with methods they can "
            "settle themselves (e.g. cash on delivery) and card buyers "
            "are handed off to the storefront checkout."
        ),
    )
    agent_stripe_delegated_enabled = models.BooleanField(
        _("Agent Stripe Delegated Payments"),
        default=False,
        help_text=_(
            "Allow AI-agent platforms to complete Stripe payments "
            "server-side with SharedPaymentTokens on this store. "
            "Requires the tenant's Stripe account to be enrolled in "
            "Stripe Agentic Commerce."
        ),
    )
    chat_api_key = EncryptedCharField(
        _("Chat API Key"),
        max_length=255,
        db_column="chat_api_key_encrypted",
        blank=True,
        default="",
        help_text=_(
            "LLM API key for the storefront chat assistant (served to "
            "the agent gateway only via the internal-token-gated "
            "tenant resolve endpoint). Each tenant brings their own "
            "key — own quota, consent, and billing. Never expose to "
            "the browser. Empty disables the chat assistant for this "
            "tenant."
        ),
    )

    # Audit trail for the platform control plane (plan changes,
    # suspend/activate, credential edits) — so Unfold's History tab
    # (LogEntry, populated by the admin lifecycle actions) can be
    # cross-checked against actual field-level diffs. ``tenant`` is
    # SHARED_APPS-only (public schema), and so is AUTH_USER_MODEL, so
    # unlike the tenant-schema shipment/Product histories this is a
    # same-schema FK — no ``user_db_constraint=False`` needed here.
    # Excludes the live credential/secret fields: duplicating a
    # merchant's Stripe/ACS/BoxNow/Viva secret into every historical
    # snapshot (one per save — including every suspend/activate) would
    # sprawl plaintext secrets into a table with no dedicated retention
    # or access policy of its own.
    history = HistoricalRecords(
        excluded_fields=[
            "stripe_secret_key",
            "meta_capi_access_token",
            "cloudflare_api_token",
            "resend_api_key",
            "resend_webhook_secret",
            "viva_wallet_api_key",
            "viva_wallet_client_secret",
            "viva_wallet_webhook_verification_key",
            "acs_api_key",
            "acs_company_password",
            "acs_user_password",
            "box_now_client_secret",
            "box_now_webhook_secret",
            "acp_bearer_token",
            "chat_api_key",
            "mydata_subscription_key",
            # A cache key component, not a fact about the store.
            "cache_generation",
        ],
    )

    auto_create_schema = True

    objects = TenantQuerySet.as_manager()

    def save(self, *args, **kwargs):
        """Bump ``cache_generation`` in the same UPDATE as the write.

        On every update, whatever changed: the cached resolve payload is
        built from most of this row, and a list of the fields it reads
        would be one more thing to keep in step. Django refreshes an
        expression-assigned field from the UPDATE's ``RETURNING``
        (``Model._save_table``), so the instance holds the new integer
        again before ``post_save`` runs. An empty ``update_fields``
        stays the no-op Django makes it, and an insert starts at the
        column default.
        """
        update_fields = kwargs.get("update_fields")
        if self._state.adding or (
            update_fields is not None and not update_fields
        ):
            return super().save(*args, **kwargs)

        previous = self.cache_generation
        self.cache_generation = models.F("cache_generation") + 1
        if update_fields is not None:
            kwargs["update_fields"] = {*update_fields, "cache_generation"}
        try:
            return super().save(*args, **kwargs)
        except BaseException:
            # A failed save must not leave an expression on the instance
            # for the next reader to build a cache key from.
            self.cache_generation = previous
            raise

    def delete(
        self, using=None, keep_parents=False, *, force_drop: bool = False
    ):
        """Block deletion of protected tenants.

        Raises ``ValidationError`` for a protected tenant
        (``tenant.lifecycle.is_protected_tenant``). All other tenants
        pass through to the django-tenants ``TenantMixin.delete()``
        which respects the ``force_drop`` kwarg to optionally drop the
        Postgres schema.

        Parameters
        ----------
        force_drop:
            When True the underlying Postgres schema is dropped before
            the row is removed. Defaults to False so that accidental
            row deletion does not silently destroy tenant data.
        """
        from tenant.lifecycle import is_protected_tenant

        if is_protected_tenant(self):
            raise ValidationError(
                _(
                    "Tenant '%(schema)s' is a protected system tenant "
                    "and cannot be deleted."
                )
                % {"schema": self.schema_name}
            )
        return super().delete(
            using=using, keep_parents=keep_parents, force_drop=force_drop
        )

    @property
    def is_suspended(self) -> bool:
        """Whether ``Tenant.objects.active()`` leaves this store out:
        suspended, or switched off. The row's form of
        ``TenantQuerySet._ACTIVE``, for a reader holding the bound row."""
        return not (self.is_active and self.suspended_at is None)

    class Meta:
        verbose_name = _("Tenant")
        verbose_name_plural = _("Tenants")
        constraints = [
            # Enforced by the database, not only by the admin form: two
            # platform storefronts would both render the bundled brand.
            models.UniqueConstraint(
                fields=["is_platform_storefront"],
                condition=models.Q(is_platform_storefront=True),
                name="tenant_single_platform_storefront",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self) -> None:
        super().clean()
        validate_schema_name(self)
        validate_stripe_publishable_key(self)
        validate_stripe_secret_key(self)
        validate_available_locales(self)
        validate_store_description_i18n(self)
        validate_legal_documents_for_new_locales(self)
        validate_meta_pixel_id(self)
        validate_tiktok_pixel_id(self)
        validate_openai_pixel_id(self)
        validate_clarity_project_id(self)
        validate_ga_tracking_id(self)
        validate_google_ads(self)
        validate_social_urls(self)
        validate_box_now_partner_id(self)
        validate_cloudflare_credentials(self)
        validate_resend_credentials(self)

    @property
    def published_indexnow_key(self) -> str:
        """The IndexNow key the storefront serves at ``/<key>.txt``: empty
        unless the store switched IndexNow on (``indexnow.service``)."""
        from indexnow.service import published_key

        return published_key(self)

    @property
    def served_locales(self) -> list[str]:
        """The languages the storefront serves this store in: the
        ``available_locales``, or only the default one when none are set."""
        return list(self.available_locales or [self.default_locale])
