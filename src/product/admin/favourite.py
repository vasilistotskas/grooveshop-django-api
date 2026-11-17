from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    AutocompleteSelectFilter,
    RangeDateTimeFilter,
)

from admin.base import (
    BaseModelAdmin,
)
from admin.displays import (
    format_dt,
)
from admin.filters import (
    RelatedDropdownFilter,
)
from product.models.favourite import ProductFavourite


@admin.register(ProductFavourite)
class ProductFavouriteAdmin(BaseModelAdmin):
    list_display = (
        "user_display",
        "product_display",
        "created_display",
    )
    date_hierarchy = "created_at"
    list_filter = [
        ("created_at", RangeDateTimeFilter),
        ("user", AutocompleteSelectFilter),
        ("product", AutocompleteSelectFilter),
        ("product__category", RelatedDropdownFilter),
    ]
    search_fields = [
        "user__email",
        "user__username",
        "product__translations__name",
        "product__sku",
    ]
    list_select_related = ["user", "product"]
    autocomplete_fields = ("product", "user")
    readonly_fields = ("created_at", "updated_at", "uuid")

    def get_queryset(self, request):
        # ``product_display`` calls ``safe_translation_getter``, which
        # fires one ``ProductTranslation`` query per row without
        # prefetch.
        return (
            super()
            .get_queryset(request)
            .select_related("user", "product")
            .prefetch_related("product__translations")
        )

    @admin.display(description=_("User"))
    def user_display(self, obj):
        return f"{obj.user.email} (#{obj.user.id})"

    @admin.display(description=_("Product"))
    def product_display(self, obj):
        name = obj.product.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed Product")
        return f"{name} (#{obj.product.sku[:8]})"

    @admin.display(description=_("Created"), ordering="created_at")
    def created_display(self, obj):
        return format_dt(obj.created_at)
