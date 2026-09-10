from __future__ import annotations

from rest_framework import serializers

from tenant.models import Tenant, TenantDomain


class TenantConfigSerializer(serializers.Serializer):
    """Public (AllowAny) serializer for the /api/v1/tenant/resolve endpoint.

    Only fields that are safe to expose to unauthenticated callers should
    appear here.  Secrets and billing-sensitive data belong exclusively in
    TenantAdminSerializer.
    """

    # --- Core identity ---
    schema_name = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    store_name = serializers.CharField(read_only=True)
    store_description = serializers.CharField(read_only=True)

    # --- Assets ---
    logo_light_url = serializers.CharField(read_only=True, allow_blank=True)
    logo_dark_url = serializers.CharField(read_only=True, allow_blank=True)
    favicon_url = serializers.CharField(read_only=True, allow_blank=True)

    # --- Platform storefront + SEO attribution ---
    # ``required=False``, NOT ``read_only=True`` — see the
    # ``openai_pixel_id`` note below: a read-only field is REQUIRED in
    # the generated schema, and a frontend-first deploy would then
    # reject every resolve from a backend that predates the field.
    is_platform_storefront = serializers.BooleanField(required=False)
    seo_author = serializers.CharField(required=False, allow_blank=True)
    google_site_verification = serializers.CharField(
        required=False, allow_blank=True
    )
    pinterest_domain_verify = serializers.CharField(
        required=False, allow_blank=True
    )

    # --- Theme ---
    primary_color = serializers.CharField(read_only=True)
    neutral_color = serializers.CharField(read_only=True)
    accent_hex = serializers.CharField(read_only=True)
    success_hex = serializers.CharField(read_only=True)
    warning_hex = serializers.CharField(read_only=True)
    error_hex = serializers.CharField(read_only=True)
    info_hex = serializers.CharField(read_only=True)
    theme_preset = serializers.CharField(read_only=True)
    theme_metadata = serializers.JSONField(read_only=True)

    # --- Localisation ---
    default_locale = serializers.CharField(read_only=True)
    # Locales the storefront may serve for this tenant. Empty = single
    # language on default_locale (see Tenant.available_locales).
    # ListField rather than JSONField so the generated OpenAPI type is
    # ``string[]`` instead of ``unknown`` — the storefront narrows this
    # without a cast.
    # ``required=False`` so the generated contract marks it OPTIONAL.
    # Without it the storefront's Zod schema rejects any response from a
    # backend that predates the field — and since Argo rolls the
    # frontend and backend independently, a frontend-first deploy would
    # fail tenant-config validation for EVERY tenant and 503 the whole
    # platform. Verified locally against a pre-field backend.
    # NOT ``read_only``: drf-spectacular treats every read-only field
    # as always present in the response and puts it in ``required``,
    # which makes the storefront's generated Zod schema REJECT any
    # response from a backend that predates the field. Argo rolls the
    # frontend and backend as separate Deployments, so a new frontend
    # pod can briefly talk to an old backend pod — and a required field
    # there fails tenant-config validation for EVERY tenant and 503s
    # the whole platform (observed locally against a pre-field
    # backend). ``required=False`` on a writable declaration is what
    # emits an OPTIONAL field. The serializer is output-only anyway:
    # ``/tenant/resolve`` never deserialises it.
    available_locales = serializers.ListField(
        child=serializers.CharField(), required=False
    )
    default_currency = serializers.CharField(read_only=True)

    # --- Domain ---
    primary_domain = serializers.SerializerMethodField()
    # The tenant's API origin hostname (e.g. ``api.tenant.com``).
    # Browser-side consumers (OAuth provider-redirect form action, the
    # notifications WebSocket, CSP connect-src) MUST dial the tenant's
    # own API host — the env-frozen platform host resolves to tenant
    # #1's schema and carries the wrong session cookies.
    api_domain = serializers.SerializerMethodField()
    # The tenant's media/image-processing origin hostname (e.g.
    # ``assets.tenant.com``) and static-file origin hostname (e.g.
    # ``static.tenant.com``). Consumers building absolute media/static
    # URLs (the Nuxt ``mediaStream`` image provider, transactional
    # email templates) MUST dial the tenant's own asset hosts — the
    # env-frozen platform ``MEDIA_STREAM_BASE_URL``/``STATIC_BASE_URL``
    # resolve to tenant #1's asset origin.
    assets_domain = serializers.SerializerMethodField()
    static_domain = serializers.SerializerMethodField()

    # --- Feature flags ---
    loyalty_enabled = serializers.BooleanField(read_only=True)
    blog_enabled = serializers.BooleanField(read_only=True)
    promotions_enabled = serializers.BooleanField(read_only=True)
    gift_cards_enabled = serializers.BooleanField(read_only=True)
    # Raw plan flag, not folded — the storefront's two-tier gate reads
    # the plan half here and the B2B_WHOLESALE_ENABLED runtime half via
    # /settings/get (the loyalty/gift-cards pattern). Folding is for
    # gateway-consumed values only, and the gateway stays retail-only.
    b2b_enabled = serializers.BooleanField(read_only=True)
    # ``required=False``, NOT ``read_only=True`` — see the
    # ``available_locales`` note: a read-only field is REQUIRED in the
    # generated schema, and a frontend-first deploy would then reject
    # every resolve from a backend that predates the field.
    recommendations_enabled = serializers.BooleanField(required=False)
    agent_stripe_delegated_enabled = serializers.BooleanField(read_only=True)
    # EFFECTIVE agent-commerce gates, consumed by the agent gateway:
    # plan flag AND the tenant-schema extra-setting, folded here so
    # the gateway reads ONE authoritative value per surface. Read
    # under the tenant's schema — this serializer runs in whatever
    # schema the resolve request happened to hit.
    agent_commerce_enabled = serializers.SerializerMethodField()
    product_feeds_enabled = serializers.SerializerMethodField()

    def get_agent_commerce_enabled(self, obj) -> bool:
        from django_tenants.utils import schema_context
        from extra_settings.models import Setting

        if not obj.agent_commerce_enabled:
            return False
        with schema_context(obj.schema_name):
            return bool(Setting.get("AGENT_COMMERCE_ENABLED", default=True))

    def get_product_feeds_enabled(self, obj) -> bool:
        from django_tenants.utils import schema_context
        from extra_settings.models import Setting

        # Subordinate to the agent-commerce gate.
        if not self.get_agent_commerce_enabled(obj):
            return False
        with schema_context(obj.schema_name):
            return bool(Setting.get("PRODUCT_FEEDS_ENABLED", default=True))

    # Provider codes an AI agent can settle on its own, in the order the
    # merchant presents them. The agent gateway advertises one UCP payment
    # instrument per entry under the ``space.grooveshop.payments`` handler,
    # so an agent can place the order without handing the buyer to a
    # browser. Offline methods qualify because they need no payment
    # credential — the buyer settles with the carrier. Online methods are
    # excluded on purpose: they require the buyer to authenticate at the
    # PSP, which UCP models as an escalation, not a payment handler.
    #
    # Served here rather than fetched per request so UCP discovery stays a
    # cached, single-round-trip lookup. The authoritative per-checkout set
    # is still resolved from live pay-way data in checkout responses.
    # Effective hosted-payment gate: platform flag AND the merchant's
    # extra-setting, subordinate to agent-commerce. The agent gateway
    # advertises the space.grooveshop.payments.hosted_selection
    # extension — and accepts the pay-way id it carries — only while
    # this is true.
    agent_hosted_payment_enabled = serializers.SerializerMethodField()

    def get_agent_hosted_payment_enabled(self, obj) -> bool:
        from django_tenants.utils import schema_context
        from extra_settings.models import Setting

        if not self.get_agent_commerce_enabled(obj):
            return False
        if not obj.agent_hosted_payment_enabled:
            return False
        with schema_context(obj.schema_name):
            return bool(
                Setting.get("AGENT_HOSTED_PAYMENT_ENABLED", default=True)
            )

    agent_payment_instruments = serializers.SerializerMethodField()

    def get_agent_payment_instruments(self, obj) -> list[str]:
        from django_tenants.utils import schema_context

        from pay_way.models import PayWay

        # Subordinate to the agent-commerce gate: a tenant with the
        # surface off advertises no agent-completable payment at all.
        if not self.get_agent_commerce_enabled(obj):
            return []
        with schema_context(obj.schema_name):
            # list() forces evaluation INSIDE the schema context: a lazy
            # queryset would run its query after the context exits, against
            # whatever schema the connection happened to be left on.
            # Everything an agent can complete WITHOUT redirecting the
            # shopper to a hosted card page — i.e. every settlement
            # except ONLINE. Reading ``settlement`` rather than the
            # deprecated ``is_online_payment`` mirror also means the
            # locker-terminal instrument (BoxNow PAY ON THE GO) is
            # advertised correctly instead of being lumped in with
            # courier cash-on-delivery.
            from pay_way.enum.settlement import PaySettlement

            codes = list(
                PayWay.objects.filter(active=True)
                .exclude(settlement=PaySettlement.ONLINE)
                .exclude(provider_code="")
                .order_by("sort_order", "id")
                .values_list("provider_code", flat=True)
            )
        # Distinct, first occurrence wins. Several pay-way rows may share
        # a provider code (a merchant offering cash on delivery through
        # two carriers, say), but each code maps to ONE agent payment
        # instrument, and a repeated instrument is not a thing UCP can
        # advertise.
        return list(dict.fromkeys(codes))

    # --- Payments (public key only) ---
    # Public Stripe publishable key — pk_test_* / pk_live_* only.
    # Empty string means Stripe is not configured for this tenant —
    # there is no platform-wide fallback.
    stripe_publishable_key = serializers.CharField(read_only=True)

    # --- CSP ---
    # Additional CSP origins for connect-src/img-src/script-src/frame-src.
    allowed_csp_sources = serializers.ListField(
        child=serializers.CharField(), read_only=True
    )

    # --- Analytics (public IDs only) ---
    meta_pixel_id = serializers.CharField(read_only=True)
    tiktok_pixel_id = serializers.CharField(read_only=True)
    # ``required=False``, NOT ``read_only=True`` like the two pixel
    # fields above — see the ``available_locales`` note. A read-only
    # field is emitted as REQUIRED in the schema, so the storefront's
    # generated Zod would reject any response from a backend that
    # predates it. Argo rolls the frontend and backend as separate
    # Deployments, so a frontend-first deploy would fail tenant-config
    # validation for EVERY tenant and 503 the whole platform. The two
    # existing pixel fields are safe only because they are already in
    # the contract on both sides.
    openai_pixel_id = serializers.CharField(required=False)
    ga_tracking_id = serializers.CharField(read_only=True)

    # --- Authentication ---
    totp_issuer = serializers.CharField(read_only=True)

    # --- Social Links ---
    socials_discord = serializers.CharField(read_only=True, allow_blank=True)
    socials_facebook = serializers.CharField(read_only=True, allow_blank=True)
    socials_instagram = serializers.CharField(read_only=True, allow_blank=True)
    socials_pinterest = serializers.CharField(read_only=True, allow_blank=True)
    socials_reddit = serializers.CharField(read_only=True, allow_blank=True)
    socials_tiktok = serializers.CharField(read_only=True, allow_blank=True)
    socials_linkedin = serializers.CharField(read_only=True, allow_blank=True)
    socials_twitter = serializers.CharField(read_only=True, allow_blank=True)
    socials_youtube = serializers.CharField(read_only=True, allow_blank=True)

    # --- Shipping (public partner ID) ---
    box_now_partner_id = serializers.CharField(read_only=True)

    # NOTE: ``plan`` is intentionally excluded — it is billing-sensitive
    # and must not be exposed to unauthenticated callers via tenant/resolve.
    # Platform admins can read it via TenantAdminSerializer.

    # NOTE: ``from_email``, ``contact_email``, ``meta_capi_access_token``,
    # ``meta_capi_dataset_id``, all Viva Wallet keys, all ACS credentials,
    # ``box_now_client_id``, ``box_now_client_secret``, ``box_now_warehouse_id``,
    # ``box_now_notify_phone``, ``stripe_secret_key``, and
    # ``chat_api_key`` are intentionally excluded — they are secrets or
    # internal config that must never be served to anonymous callers.
    # Only available via TenantAdminSerializer (``chat_api_key`` is
    # additionally appended to tenant_resolve responses for the agent
    # gateway only, after an X-Internal-Token check — never cached).

    def get_primary_domain(self, obj: Tenant) -> str:
        domain = obj.domains.filter(is_primary=True).first()
        return domain.domain if domain else ""

    def get_api_domain(self, obj: Tenant) -> str:
        from core.utils.tenant_urls import (
            resolve_tenant_api_domain,
        )

        return resolve_tenant_api_domain(obj)

    def get_assets_domain(self, obj: Tenant) -> str:
        from core.utils.tenant_urls import (
            resolve_tenant_assets_domain,
        )

        return resolve_tenant_assets_domain(obj)

    def get_static_domain(self, obj: Tenant) -> str:
        from core.utils.tenant_urls import (
            resolve_tenant_static_domain,
        )

        return resolve_tenant_static_domain(obj)


class TenantDomainSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantDomain
        fields = ["id", "domain", "is_primary"]


class TenantAdminSerializer(serializers.ModelSerializer):
    """Full serializer for platform-admin access only.

    Includes all fields from TenantConfigSerializer PLUS billing-sensitive
    data (plan, paid_until), email config, carrier credentials, and all
    other secrets.  This serializer must never be exposed to anonymous
    callers.
    """

    domains = TenantDomainSerializer(many=True, read_only=True)

    class Meta:
        model = Tenant
        fields = [
            # --- Core identity ---
            "id",
            "uuid",
            "schema_name",
            "name",
            "slug",
            "owner_email",
            # --- Lifecycle state (read-only; see read_only_fields) ---
            "is_active",
            "suspended_at",
            "suspended_reason",
            # --- Plan & Billing (excluded from public serializer) ---
            "plan",
            "paid_until",
            # --- Branding ---
            "store_name",
            "store_description",
            "default_locale",
            "available_locales",
            "default_currency",
            # --- Assets ---
            "logo_light_url",
            "logo_dark_url",
            "favicon_url",
            # --- Platform storefront + SEO attribution ---
            "is_platform_storefront",
            "seo_author",
            "google_site_verification",
            "pinterest_domain_verify",
            # --- Theme ---
            "primary_color",
            "neutral_color",
            "accent_hex",
            "success_hex",
            "warning_hex",
            "error_hex",
            "info_hex",
            "theme_preset",
            "theme_metadata",
            # --- Features ---
            "loyalty_enabled",
            "blog_enabled",
            "promotions_enabled",
            "gift_cards_enabled",
            "recommendations_enabled",
            # --- Payments (public keys) ---
            "stripe_connect_account_id",
            "stripe_publishable_key",
            # --- CSP ---
            "allowed_csp_sources",
            # --- Analytics ---
            "meta_pixel_id",
            "tiktok_pixel_id",
            "openai_pixel_id",
            "ga_tracking_id",
            "meta_capi_access_token",
            "meta_capi_dataset_id",
            # --- Authentication ---
            "totp_issuer",
            # --- Agentic Commerce ---
            "chat_api_key",
            "acp_bearer_token",
            "agent_hosted_payment_enabled",
            "agent_stripe_delegated_enabled",
            "agent_commerce_enabled",
            # --- Social Links ---
            "socials_discord",
            "socials_facebook",
            "socials_instagram",
            "socials_pinterest",
            "socials_reddit",
            "socials_tiktok",
            "socials_linkedin",
            "socials_twitter",
            "socials_youtube",
            # --- Email ---
            "from_email",
            "contact_email",
            # --- Payments — Viva Wallet ---
            "viva_wallet_merchant_id",
            "viva_wallet_api_key",
            "viva_wallet_client_id",
            "viva_wallet_client_secret",
            "viva_wallet_webhook_verification_key",
            "viva_wallet_source_code",
            "viva_wallet_live_mode",
            # --- Payments — Stripe (secrets) ---
            "stripe_secret_key",
            # --- Shipping — ACS ---
            "acs_api_key",
            "acs_company_id",
            "acs_company_password",
            "acs_user_id",
            "acs_user_password",
            "acs_billing_code",
            "acs_station_origin",
            # --- Shipping — BoxNow ---
            "box_now_partner_id",
            "box_now_client_id",
            "box_now_client_secret",
            "box_now_warehouse_id",
            "box_now_notify_phone",
            "box_now_webhook_secret",
            # --- Timestamps ---
            "created_at",
            "updated_at",
            # --- Related ---
            "domains",
        ]
        # ``is_active`` and its two companions are lifecycle STATE, not
        # settings: writing them through this serializer bypassed every
        # gate ``tenant.lifecycle`` exists to enforce. Verified against
        # the live endpoint:
        #
        # * ``PATCH {"isActive": false}`` answered 200 leaving
        #   ``suspended_at`` NULL and dispatching no media flush, so the
        #   store kept serving processed images for the cache TTL (up to
        #   360 days) and ``destroy_refusal`` answered "not_suspended" —
        #   a store suspended that way can never be destroyed through the
        #   gated path.
        # * ``PATCH {"isActive": true}`` answered 200 leaving
        #   ``suspended_at`` and ``suspended_reason`` in place, so the
        #   NEXT genuine suspension kept the stale anchor: measured, a
        #   freshly suspended store reported a 30-day-old anchor and
        #   ``destroy_refusal`` of ``None``. The 24h cooldown that makes
        #   a mistaken suspension reversible was already spent.
        # * a PROTECTED tenant flipped to inactive, which both
        #   ``suspend_tenant`` and ``activate_tenant`` refuse.
        #
        # The state changes live on the viewset's ``suspend``/``activate``
        # actions, which call the lifecycle functions — the same shape
        # ``destroy`` already had.
        read_only_fields = [
            "schema_name",
            "uuid",
            "created_at",
            "updated_at",
            "is_active",
            "suspended_at",
            "suspended_reason",
        ]


class TenantSuspendRequestSerializer(serializers.Serializer):
    """Why a store is being taken offline.

    Required, not optional: ``suspended_reason`` is the one record of
    whether a suspension was billing, abuse or an operator mistake, and
    ``suspend_tenant`` deliberately refuses to relabel it on a second
    call — so an empty first reason can never be corrected.
    """

    reason = serializers.CharField(max_length=255, allow_blank=False)


class TenantLifecycleStateSerializer(serializers.Serializer):
    """The lifecycle state after a suspend/activate call.

    ``changed`` distinguishes "this call moved the store" from "it was
    already there", which is what the lifecycle functions return and
    what an operator retrying a request needs to know.
    """

    changed = serializers.BooleanField(read_only=True)
    is_active = serializers.BooleanField(source="tenant.is_active")
    suspended_at = serializers.DateTimeField(
        source="tenant.suspended_at", allow_null=True
    )
    suspended_reason = serializers.CharField(
        source="tenant.suspended_reason", allow_blank=True
    )


class MerchantLegalIdentitySerializer(serializers.Serializer):
    """The seller identity a storefront is legally required to publish.

    Read-only and AllowAny by design: every field here is information the
    merchant is *obliged* to make public (e-Commerce Directive art. 5,
    N. 4919/2022 art. 22), so there is nothing to protect. Blanks are
    returned as blanks rather than omitted — a consumer of this endpoint
    needs to distinguish "not provided" from "not applicable", and
    ``missing_fields`` names the ones that are legally required.
    """

    name = serializers.CharField(read_only=True, allow_blank=True)
    legal_form = serializers.CharField(read_only=True, allow_blank=True)
    vat_id = serializers.CharField(read_only=True, allow_blank=True)
    tax_office = serializers.CharField(read_only=True, allow_blank=True)
    registration_number = serializers.CharField(
        read_only=True, allow_blank=True
    )
    business_activity = serializers.CharField(read_only=True, allow_blank=True)
    address_line_1 = serializers.CharField(read_only=True, allow_blank=True)
    address_line_2 = serializers.CharField(read_only=True, allow_blank=True)
    city = serializers.CharField(read_only=True, allow_blank=True)
    postal_code = serializers.CharField(read_only=True, allow_blank=True)
    country = serializers.CharField(read_only=True, allow_blank=True)
    phone = serializers.CharField(read_only=True, allow_blank=True)
    email = serializers.CharField(read_only=True, allow_blank=True)
    in_liquidation = serializers.BooleanField(read_only=True)
    # Named so the storefront can render a compliance warning to staff
    # instead of silently publishing an incomplete disclosure.
    missing_fields = serializers.ListField(
        child=serializers.CharField(), read_only=True
    )
    is_complete = serializers.BooleanField(read_only=True)
