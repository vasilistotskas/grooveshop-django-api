from __future__ import annotations

from rest_framework import serializers

from tenant.models import Tenant, TenantDomain


class TenantConfigSerializer(serializers.Serializer):
    schema_name = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    store_name = serializers.CharField(read_only=True)
    store_description = serializers.CharField(read_only=True)
    logo_light_url = serializers.URLField(read_only=True)
    logo_dark_url = serializers.URLField(read_only=True)
    favicon_url = serializers.URLField(read_only=True)
    primary_color = serializers.CharField(read_only=True)
    neutral_color = serializers.CharField(read_only=True)
    accent_hex = serializers.CharField(read_only=True)
    success_hex = serializers.CharField(read_only=True)
    warning_hex = serializers.CharField(read_only=True)
    error_hex = serializers.CharField(read_only=True)
    info_hex = serializers.CharField(read_only=True)
    theme_preset = serializers.CharField(read_only=True)
    theme_metadata = serializers.JSONField(read_only=True)
    default_locale = serializers.CharField(read_only=True)
    default_currency = serializers.CharField(read_only=True)
    primary_domain = serializers.SerializerMethodField()
    loyalty_enabled = serializers.BooleanField(read_only=True)
    blog_enabled = serializers.BooleanField(read_only=True)
    plan = serializers.CharField(read_only=True)

    def get_primary_domain(self, obj: Tenant) -> str:
        domain = obj.domains.filter(is_primary=True).first()
        return domain.domain if domain else ""


class TenantDomainSerializer(serializers.ModelSerializer):
    class Meta:
        model = TenantDomain
        fields = ["id", "domain", "is_primary"]


class TenantAdminSerializer(serializers.ModelSerializer):
    domains = TenantDomainSerializer(many=True, read_only=True)

    class Meta:
        model = Tenant
        fields = [
            "id",
            "uuid",
            "schema_name",
            "name",
            "slug",
            "owner_email",
            "is_active",
            "plan",
            "paid_until",
            "store_name",
            "store_description",
            "default_locale",
            "default_currency",
            "logo_light_url",
            "logo_dark_url",
            "favicon_url",
            "primary_color",
            "neutral_color",
            "accent_hex",
            "success_hex",
            "warning_hex",
            "error_hex",
            "info_hex",
            "theme_preset",
            "theme_metadata",
            "loyalty_enabled",
            "blog_enabled",
            "stripe_connect_account_id",
            "created_at",
            "updated_at",
            "domains",
        ]
        read_only_fields = ["schema_name", "uuid", "created_at", "updated_at"]
