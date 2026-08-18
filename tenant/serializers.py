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

    # --- Feature flags ---
    loyalty_enabled = serializers.BooleanField(read_only=True)
    blog_enabled = serializers.BooleanField(read_only=True)
    agent_stripe_delegated_enabled = serializers.BooleanField(read_only=True)

    # --- Payments (public key only) ---
    # Public Stripe publishable key — pk_test_* / pk_live_* only.
    # Empty string means "use the platform-wide key from settings."
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

    # --- Bot Protection (site key only) ---
    turnstile_site_key = serializers.CharField(read_only=True)

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
    # ``box_now_notify_phone``, ``turnstile_secret_key``, and
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
            # --- Bot Protection ---
            "turnstile_site_key",
            "turnstile_secret_key",
            # --- Agentic Commerce ---
            "chat_api_key",
            "acp_bearer_token",
            "agent_stripe_delegated_enabled",
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
            "stripe_use_platform_account",
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
