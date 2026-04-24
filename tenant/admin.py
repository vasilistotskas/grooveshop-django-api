from django.contrib import admin
from django.db import connection

from tenant.models import Tenant, TenantDomain, UserTenantMembership


class TenantDomainInline(admin.TabularInline):
    model = TenantDomain
    extra = 1


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "slug",
        "schema_name",
        "plan",
        "is_active",
        "created_at",
    ]
    list_filter = ["is_active", "plan"]
    search_fields = ["name", "slug", "owner_email"]
    readonly_fields = ["schema_name", "uuid", "created_at", "updated_at"]
    inlines = [TenantDomainInline]

    fieldsets = [
        (
            None,
            {
                "fields": [
                    "name",
                    "slug",
                    "schema_name",
                    "owner_email",
                    "is_active",
                    "uuid",
                ]
            },
        ),
        (
            "Plan & Billing",
            {"fields": ["plan", "paid_until", "stripe_connect_account_id"]},
        ),
        (
            "Branding",
            {
                "fields": [
                    "store_name",
                    "store_description",
                    "default_locale",
                    "default_currency",
                    "logo_light_url",
                    "logo_dark_url",
                    "favicon_url",
                ]
            },
        ),
        (
            "Theme",
            {
                "fields": [
                    "primary_color",
                    "neutral_color",
                    "accent_hex",
                    "success_hex",
                    "warning_hex",
                    "error_hex",
                    "info_hex",
                    "theme_preset",
                    "theme_metadata",
                ]
            },
        ),
        (
            "Features",
            {"fields": ["loyalty_enabled", "blog_enabled"]},
        ),
        (
            "Timestamps",
            {"fields": ["created_at", "updated_at"]},
        ),
    ]

    def has_module_permission(self, request):
        return connection.schema_name == "public"


@admin.register(TenantDomain)
class TenantDomainAdmin(admin.ModelAdmin):
    list_display = ["domain", "tenant", "is_primary"]
    list_filter = ["is_primary"]
    search_fields = ["domain"]

    def has_module_permission(self, request):
        return connection.schema_name == "public"


@admin.register(UserTenantMembership)
class UserTenantMembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "tenant", "role", "is_active", "created_at"]
    list_filter = ["role", "is_active", "tenant"]
    search_fields = ["user__email", "user__username", "tenant__name"]
    autocomplete_fields = ["user", "tenant"]
    readonly_fields = ["created_at", "updated_at"]

    def has_module_permission(self, request):
        # Membership admin is a platform-wide surface — like Tenant and
        # TenantDomain, it must not leak into tenant-scoped admin.
        return connection.schema_name == "public"
