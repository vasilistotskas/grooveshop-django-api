from django.contrib import admin
from django.db.models import Count
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
)
from unfold.decorators import display

from admin.base import (
    BaseTranslatableAdmin,
)
from admin.displays import (
    format_dt,
    header_two_line,
)
from product.models.category import ProductCategory
from product.models.category_image import ProductCategoryImage
from product.models.product import (
    Product,
)


class ProductCategoryImageInline(TabularInline):
    model = ProductCategoryImage
    extra = 0
    fields = ("image", "image_type", "active")

    tab = True
    show_change_link = True


@admin.register(ProductCategory)
class ProductCategoryAdmin(BaseTranslatableAdmin):
    ordering_field = "sort_order"
    hide_ordering_field = True
    # ``Count()`` annotations below strip the model's default
    # ``Meta.ordering`` (Django drops default ordering on GROUP BY
    # queries) — explicit ordering survives the annotation.
    ordering = ("sort_order",)
    list_display = (
        "category_info",
        "active",
        "subcategories_display",
        "created_display",
        "products_count_display",
        "recursive_products_display",
    )
    list_display_links = ("category_info",)
    search_fields = ("translations__name", "translations__description")
    list_filter = [
        "active",
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
        "parent",
    ]
    inlines = [ProductCategoryImageInline]
    readonly_fields = [
        "uuid",
        "created_at",
        "updated_at",
        "products_count_display",
        "recursive_products_display",
    ]

    fieldsets = (
        (
            _("Category Information"),
            {
                "fields": ("parent", "slug", "active"),
            },
        ),
        (
            _("Content"),
            {
                "fields": ("name", "description", "footer_text"),
            },
        ),
        (
            _("SEO"),
            {
                "fields": ("seo_title", "seo_description", "seo_keywords"),
                "classes": ("collapse",),
            },
        ),
        (
            _("Analytics"),
            {
                "fields": (
                    "products_count_display",
                    "recursive_products_display",
                ),
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
        qs = super().get_queryset(request)
        qs = ProductCategory.objects.add_related_count(
            qs,
            Product,
            "category",
            "products_cumulative_count",
            cumulative=True,
        )
        qs = ProductCategory.objects.add_related_count(
            qs, Product, "category", "products_count", cumulative=False
        )
        # Direct child count via annotation instead of a per-row
        # ``get_children().count()`` query.
        qs = qs.annotate(children_count=Count("children", distinct=True))
        # ``image_preview`` reads ``instance.main_image``, which only
        # uses a cache when the queryset supplied one — otherwise it is
        # one query per row. Measured: 6 queries for 6 rows. The sibling
        # admins all carry this prefetch with a comment saying why; this
        # one was missed.
        return qs.with_main_image()

    def get_prepopulated_fields(self, request, obj=None):
        return {"slug": ("name",)}

    @display(
        description=_("Category"), ordering="translations__name", header=True
    )
    def category_info(self, instance):
        name = instance.safe_translation_getter("name", any_language=True) or _(
            "Unnamed Category"
        )
        main_image = instance.main_image
        return header_two_line(
            name,
            _("Level %(level)s") % {"level": instance.level},
            image_path=main_image.image.url
            if main_image and main_image.image
            else None,
            squared=True,
        )

    @admin.display(description=_("Subcategories"), ordering="children_count")
    def subcategories_display(self, instance):
        return getattr(instance, "children_count", 0)

    @admin.display(description=_("Created"), ordering="created_at")
    def created_display(self, instance):
        return format_dt(instance.created_at, fmt="Y-m-d")

    @admin.display(description=_("Direct Products"), ordering="products_count")
    def products_count_display(self, instance):
        return getattr(instance, "products_count", 0)

    @admin.display(
        description=_("Total Products"), ordering="products_cumulative_count"
    )
    def recursive_products_display(self, instance):
        return getattr(instance, "products_cumulative_count", 0)
