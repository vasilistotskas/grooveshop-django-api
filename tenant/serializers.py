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
    agent_stripe_delegated_enabled = serializers.BooleanField(read_only=True)
    # EFFECTIVE agent-commerce gates, consumed by the agent gateway:
    # plan flag AND the tenant-schema extra-setting, folded here so
    # the gateway reads ONE authoritative value per surface. Read
    # under the tenant's schema — this serializer runs in whatever
    # schema the resolve request happened to hit.
    agent_commerce_enabled = serializers.SerializerMethodField()
    product_feeds_enabled = serializers.SerializerMethodField()

    def get_agent_commerce_enabled(self, obj) -> bool:
        from django_tenants.utils import schema_context  # noqa: PLC0415
        from extra_settings.models import Setting  # noqa: PLC0415

        if not obj.agent_commerce_enabled:
            return False
        with schema_context(obj.schema_name):
            return bool(Setting.get("AGENT_COMMERCE_ENABLED", default=True))

    def get_product_feeds_enabled(self, obj) -> bool:
        from django_tenants.utils import schema_context  # noqa: PLC0415
        from extra_settings.models import Setting  # noqa: PLC0415

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
    agent_payment_instruments = serializers.SerializerMethodField()

    def get_agent_payment_instruments(self, obj) -> list[str]:
        from django_tenants.utils import schema_context  # noqa: PLC0415

        from pay_way.models import PayWay  # noqa: PLC0415

        # Subordinate to the agent-commerce gate: a tenant with the
        # surface off advertises no agent-completable payment at all.
        if not self.get_agent_commerce_enabled(obj):
            return []
        with schema_context(obj.schema_name):
            # list() forces evaluation INSIDE the schema context: a lazy
            # queryset would run its query after the context exits, against
            # whatever schema the connection happened to be left on.
            codes = list(
                PayWay.objects.filter(active=True, is_online_payment=False)
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
        from core.utils.tenant_urls import (  # noqa: PLC0415
            resolve_tenant_api_domain,
        )

        return resolve_tenant_api_domain(obj)

    def get_assets_domain(self, obj: Tenant) -> str:
        from core.utils.tenant_urls import (  # noqa: PLC0415
            resolve_tenant_assets_domain,
        )

        return resolve_tenant_assets_domain(obj)

    def get_static_domain(self, obj: Tenant) -> str:
        from core.utils.tenant_urls import (  # noqa: PLC0415
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
            "is_active",
            # --- Plan & Billing (excluded from public serializer) ---
            "plan",
            "paid_until",
            # --- Branding ---
            "store_name",
            "store_description",
            "default_locale",
            "default_currency",
            # --- Assets ---
            "logo_light_url",
            "logo_dark_url",
            "favicon_url",
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
            # --- Payments (public keys) ---
            "stripe_connect_account_id",
            "stripe_publishable_key",
            # --- CSP ---
            "allowed_csp_sources",
            # --- Analytics ---
            "meta_pixel_id",
            "tiktok_pixel_id",
            "ga_tracking_id",
            "meta_capi_access_token",
            "meta_capi_dataset_id",
            # --- Authentication ---
            "totp_issuer",
            # --- Agentic Commerce ---
            "chat_api_key",
            "acp_bearer_token",
            "agent_stripe_delegated_enabled",
            "agent_commerce_enabled",
            # --- Social Links ---
            "socials_discord",
            "socials_facebook",
            "socials_instagram",
            "socials_pinterest",
            "socials_reddit",
            "socials_tiktok",
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
        read_only_fields = ["schema_name", "uuid", "created_at", "updated_at"]
