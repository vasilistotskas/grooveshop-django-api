from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from parler.admin import TranslatableAdmin
from unfold.admin import ModelAdmin

from pay_way.models import PayWay


@admin.register(PayWay)
class PayWayAdmin(ModelAdmin, TranslatableAdmin):
    list_display = [
        "name",
        "provider_code",
        "active",
        "payment_type_badge",
        "cost",
        "free_threshold",
    ]
    list_filter = ("active", "is_online_payment", "requires_confirmation")
    search_fields = (
        "translations__name",
        "provider_code",
        "translations__description",
    )
    readonly_fields = ("created_at", "updated_at")

    fieldsets = (
        (
            None,
            {
                "fields": (
                    "active",
                    "icon",
                ),
            },
        ),
        (
            _("Translatable Content"),
            {
                "fields": (
                    "name",
                    "description",
                    "instructions",
                ),
            },
        ),
        (
            _("Payment Method Details"),
            {
                "fields": (
                    "provider_code",
                    "is_online_payment",
                    "requires_confirmation",
                ),
            },
        ),
        (
            _("Payment Cost"),
            {
                "fields": (
                    "cost",
                    "free_threshold",
                ),
            },
        ),
        (
            _("Provider Configuration"),
            {
                "fields": ("configuration",),
                "classes": ("collapse",),
            },
        ),
        (
            _("Audit Information"),
            {
                "fields": ("created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def payment_type_badge(self, obj):
        if obj.is_online_payment:
            return "🌐 Online"
        elif obj.requires_confirmation:
            return "⏱️ Offline (Confirmation)"
        else:
            return "📝 Offline"

    payment_type_badge.short_description = _("Payment Type")
    payment_type_badge.admin_order_field = "is_online_payment"
