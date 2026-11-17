from django.contrib import admin
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
)

from admin.base import (
    BaseModelAdmin,
)
from product.models.brand import Brand


@admin.register(Brand)
class BrandAdmin(BaseModelAdmin):
    """Registry of product brands consumed by the Meta/TikTok catalog feeds.

    Products are assigned a brand from the Product page (the ``brand``
    autocomplete); this page manages the canonical brand names.
    """

    list_display = (
        "name",
        "products_count_display",
        "created_at",
    )
    search_fields = ["name"]
    list_filter = [
        ("created_at", RangeDateTimeFilter),
    ]
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
    )
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Brand"),
            {
                "fields": ("name",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "id",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(products_count=Count("products", distinct=True))
        )

    @admin.display(description=_("Products"), ordering="products_count")
    def products_count_display(self, obj):
        return obj.products_count
