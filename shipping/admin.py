from django.contrib import admin
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from django.utils.translation import pgettext_lazy
from unfold.admin import TabularInline
from unfold.decorators import display

from admin.base import BaseModelAdmin
from admin.displays import header_two_line
from pay_way.admin import PayWayShippingExclusionInline
from shipping.models import ShippingProvider, ShippingRate


class ShippingRateInline(TabularInline):
    """Per-(country, kind) price, free-threshold and weight cap."""

    model = ShippingRate
    extra = 0
    fields = (
        "country",
        "kind",
        "price",
        "free_shipping_threshold",
        "max_weight_grams",
        "is_active",
    )
    autocomplete_fields = ("country",)
    verbose_name = pgettext_lazy("shipping", "Rate")
    verbose_name_plural = _("Rates")


@admin.register(ShippingProvider)
class ShippingProviderAdmin(BaseModelAdmin):
    list_display = (
        "provider_display",
        "is_active",
        "supports_home_delivery",
        "supports_pickup_point",
        "live_mode",
        "priority",
        "rates_count",
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
    inlines = [ShippingRateInline, PayWayShippingExclusionInline]

    def get_queryset(self, request):
        return (
            super().get_queryset(request).annotate(_rates_count=Count("rates"))
        )

    @admin.display(description=_("Rates"), ordering="_rates_count")
    def rates_count(self, obj) -> int:
        return obj._rates_count

    @display(description=_("Carrier"), ordering="name", header=True)
    def provider_display(self, obj):
        """The primary logo; the pickup-point one is previewed on the
        change form."""
        return header_two_line(
            obj.name,
            obj.code,
            image_path=obj.logo.url if obj.logo else None,
            contained=True,
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
