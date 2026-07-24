from django.contrib import admin
from django.utils.html import format_html
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
        "logo_preview",
        "logo_pickup_point_preview",
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

    @admin.display(description=_("Logo"), empty_value="—")
    def logo_preview(self, obj):
        """Inline thumbnail of the primary carrier logo."""
        if not obj.logo:
            return None
        return format_html(
            '<img src="{url}" width="64" height="32" alt="" />',
            url=obj.logo.url,
        )

    @admin.display(description=_("Pickup logo"), empty_value="—")
    def logo_pickup_point_preview(self, obj):
        """Inline thumbnail of the optional pickup-point logo.

        Empty for carriers that don't differentiate pickup from home
        delivery — those rows fall back to the primary logo at render
        time, so an empty cell here is the expected state for BoxNow
        (single-kind) and for any carrier the operator hasn't given a
        distinct locker illustration.
        """
        if not obj.logo_pickup_point:
            return None
        return format_html(
            '<img src="{url}" width="64" height="32" alt="" />',
            url=obj.logo_pickup_point.url,
        )

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
                "fields": ("logo", "logo_pickup_point"),
                "description": _(
                    "Brand assets shown on the storefront's checkout "
                    "shipping picker and the order summary. "
                    "``Logo`` is used for home-delivery rows and as "
                    "the fallback for pickup-point rows. "
                    "``Pickup-point logo`` is optional — set it only "
                    "when you want the locker card to look different "
                    "from the home-delivery card for the same carrier "
                    "(e.g. an ACS Smartpoint locker illustration vs "
                    "the ACS courier mark). PNG/JPG/SVG. When both "
                    "are blank the storefront renders its bundled "
                    "default for the carrier."
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
