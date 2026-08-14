from __future__ import annotations

from rest_framework import serializers

from tenant.config import Tenant


class TenantConfigSerializer(serializers.Serializer):
    """Public (AllowAny) serializer for the /api/v1/tenant/resolve endpoint.

    Field-for-field mirror of the `multi-tenant` branch's
    ``TenantConfigSerializer`` — only fields safe to expose to
    unauthenticated callers appear here. Keep in sync with
    `git show multi-tenant:tenant/serializers.py`.
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

    # --- Feature flags ---
    loyalty_enabled = serializers.BooleanField(read_only=True)
    blog_enabled = serializers.BooleanField(read_only=True)

    # --- Payments (public key only) ---
    stripe_publishable_key = serializers.CharField(read_only=True)

    # --- CSP ---
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

    def get_primary_domain(self, obj: Tenant) -> str:
        return obj.primary_domain
