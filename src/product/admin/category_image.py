from django.contrib import admin
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
)
from unfold.decorators import display

from admin.base import (
    BaseTranslatableAdmin,
)
from admin.displays import (
    choice_label,
    header_two_line,
)
from admin.filters import (
    RelatedDropdownFilter,
)
from product.admin.variants import CATEGORY_IMAGE_TYPE_VARIANT
from product.models.category_image import ProductCategoryImage


@admin.register(ProductCategoryImage)
class ProductCategoryImageAdmin(BaseTranslatableAdmin):
    prefetch_translations = ("category__translations",)
    ordering_field = "sort_order"
    hide_ordering_field = True
    list_display = (
        "category_name",
        "image_type_label",
        "active",
        "sort_order",
        "created_at",
    )
    list_filter = [
        "image_type",
        "active",
        ("category", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    ]
    search_fields = [
        "category__translations__name",
        "translations__alt_text",
    ]
    list_select_related = ["category"]
    readonly_fields = ("created_at", "updated_at", "uuid")
    ordering = ["category", "image_type", "sort_order"]

    fieldsets = (
        (
            _("Image Information"),
            {
                "fields": ("category", "image", "image_type", "active"),
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
                "fields": ("uuid", "created_at", "updated_at"),
                "classes": ("collapse",),
            },
        ),
    )

    def get_queryset(self, request):
        # ``category_name`` calls ``safe_translation_getter``, which
        # fires one ``ProductCategoryTranslation`` query per row
        # without prefetch.
        return (
            super()
            .get_queryset(request)
            .prefetch_related("category__translations", "translations")
        )

    @display(description=_("Category"), header=True)
    def category_name(self, obj):
        return header_two_line(
            obj.category.safe_translation_getter("name", any_language=True)
            or _("Unnamed Category"),
            obj.safe_translation_getter("alt_text", any_language=True),
            image_path=obj.image.url if obj.image else None,
            squared=True,
        )

    image_type_label = choice_label(
        "image_type",
        variants=CATEGORY_IMAGE_TYPE_VARIANT,
        description=_("Type"),
    )
