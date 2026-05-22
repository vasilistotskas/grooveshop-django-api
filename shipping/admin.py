from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from admin.base import BaseModelAdmin
from pay_way.admin import PayWayShippingExclusionInline
from shipping.models import ShippingProvider


@admin.register(ShippingProvider)
class ShippingProviderAdmin(BaseModelAdmin):
    list_display = (
        "code",
        "name",
        "is_active",
        "supports_home_delivery",
        "supports_pickup_point",
        "live_mode",
        "priority",
        "logo_filename",
    )
    list_filter = (
        "is_active",
        "live_mode",
        "supports_home_delivery",
        "supports_pickup_point",
    )
    search_fields = ("code", "name")
    ordering = ("priority", "name")
    readonly_fields = ("created_at", "updated_at")
    # Same inline as on PayWayAdmin — rows where this provider is the
    # FK target. Lets ops manage exclusions from whichever side they
    # land on (per-provider sweep vs per-pay-way sweep).
    inlines = [PayWayShippingExclusionInline]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "code",
                    "name",
                    "is_active",
                    "priority",
                )
            },
        ),
        (
            _("Capabilities"),
            {
                "fields": (
                    "supports_home_delivery",
                    "supports_pickup_point",
                    "live_mode",
                )
            },
        ),
        (
            _("Branding"),
            {
                "fields": ("logo",),
                "description": _(
                    "Optional brand logo shown on the storefront's "
                    "checkout shipping picker and the order summary. "
                    "PNG/JPG/SVG supported. When blank the storefront "
                    "renders its bundled default for the carrier."
                ),
            },
        ),
        (
            _("Metadata"),
            {
                "fields": ("metadata",),
                "classes": ("collapse",),
            },
        ),
        (
            _("Timestamps"),
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )
