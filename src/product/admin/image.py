from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    AutocompleteSelectFilter,
    RangeDateTimeFilter,
)
from unfold.decorators import display

from admin.base import (
    BaseTranslatableAdmin,
)
from admin.displays import (
    header_two_line,
)
from product.models.image import ProductImage


@admin.register(ProductImage)
class ProductImageAdmin(BaseTranslatableAdmin):
    ordering_field = "sort_order"
    hide_ordering_field = True
    list_display = (
        "product_name",
        "is_main",
        "sort_order",
        "created_at",
    )
    list_filter = [
        "is_main",
        ("product", AutocompleteSelectFilter),
        ("created_at", RangeDateTimeFilter),
    ]
    search_fields = [
        "product__translations__name",
        "product__sku",
        "translations__alt_text",
    ]
    list_select_related = ["product"]
    autocomplete_fields = ("product",)
    readonly_fields = (
        "created_at",
        "updated_at",
        "uuid",
        "source_url",
        "source_sha256",
    )
    ordering = ["product", "-is_main", "sort_order"]

    fieldsets = (
        (
            _("Image Information"),
            {
                "fields": ("product", "image", "is_main"),
            },
        ),
        (
            _("Content"),
            {
                "fields": ("alt_text",),
            },
        ),
        (
            _("Organization"),
            {
                "fields": ("sort_order",),
                "classes": ("collapse",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "uuid",
                    "source_url",
                    "source_sha256",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        # ``product_name`` calls ``safe_translation_getter``, which
        # fires one ``ProductTranslation`` query per row without
        # prefetch.
        return (
            super()
            .get_queryset(request)
            .prefetch_related("product__translations")
        )

    @display(description=_("Product"), header=True)
    def product_name(self, obj):
        return header_two_line(
            obj.product.safe_translation_getter("name", any_language=True)
            or _("Unnamed Product"),
            obj.product.sku,
            image_path=obj.image.url if obj.image else None,
            squared=True,
        )
