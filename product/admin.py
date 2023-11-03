import admin_thumbnails
from django.contrib import admin
from django.contrib import messages
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from mptt.admin import DraggableMPTTAdmin
from parler.admin import TranslatableAdmin

from core.admin import ExportModelAdmin
from product.models.category import ProductCategory
from product.models.favourite import ProductFavourite
from product.models.image import ProductImage
from product.models.product import Product
from product.models.review import ProductReview


def category_update_action(category):
    def category_update(model_admin, request, queryset):
        return queryset.update(category=category)

    category_update.__name__ = "make_action_%s" % category.name
    category_update.short_description = _("Update category to %s") % category.name
    return category_update


@admin.register(ProductCategory)
class CategoryAdmin(TranslatableAdmin, DraggableMPTTAdmin):
    mptt_indent_field = "translations__name"
    list_display = (
        "tree_actions",
        "indented_title",
        "related_products_count",
        "related_products_cumulative_count",
    )
    list_display_links = ("indented_title",)
    search_fields = ("translations__name",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)

        # Add cumulative product count
        qs = ProductCategory.objects.add_related_count(
            qs, Product, "category", "products_cumulative_count", cumulative=True
        )

        # Add non cumulative product count
        qs = ProductCategory.objects.add_related_count(
            qs, Product, "category", "products_count", cumulative=False
        )
        return qs

    def get_prepopulated_fields(self, request, obj=None):
        # can't use `prepopulated_fields = ..` because it breaks the admin validation
        # for translated fields. This is the official django-parler workaround.
        return {
            "slug": ("name",),
        }

    def related_products_count(self, instance):
        return instance.products_count

    setattr(
        related_products_count,
        "short_description",
        _("Related products (for this specific category)"),
    )

    def related_products_cumulative_count(self, instance):
        return instance.products_cumulative_count

    setattr(
        related_products_cumulative_count,
        "short_description",
        _("Related products (in tree)"),
    )


@admin.register(ProductFavourite)
class FavouriteAdmin(admin.ModelAdmin):
    list_display = ["user", "product"]


@admin_thumbnails.thumbnail("image")
class ProductImageInline(admin.TabularInline):
    model = ProductImage
    exclude = ["thumbnail"]
    readonly_fields = ("id",)
    extra = 1


@admin.register(Product)
class ProductAdmin(TranslatableAdmin, ExportModelAdmin):
    list_display = [
        "id",
        "category",
        "price",
        "colored_stock",
        "boolean_status",
        "image_tag",
        "likes_counter",
        "metadata",
        "private_metadata",
    ]
    search_fields = ["id", "category__name", "translations__name", "product_code"]
    list_filter = ["category"]
    inlines = [ProductImageInline]
    readonly_fields = ("image_tag", "likes_counter")

    def get_prepopulated_fields(self, request, obj=None) -> dict:
        # can't use `prepopulated_fields = ..` because it breaks the admin validation
        # for translated fields. This is the official django-parler workaround.
        return {
            "slug": ("name",),
        }

    def boolean_status(self, obj) -> bool:
        return True if obj.active else False

    setattr(
        boolean_status,
        "boolean",
        True,
    )

    actions = [
        "export_csv",
        "export_xml",
    ]


@admin.register(ProductReview)
class ReviewAdmin(TranslatableAdmin):
    list_display = ["comment", "status", "created_at"]
    list_filter = ["status"]
    actions = ["make_published", "make_unpublished"]
    search_fields = ["translations__comment"]

    def make_published(self, request, queryset):
        updated = queryset.update(status="True")
        self.message_user(
            request,
            ngettext(
                _(
                    "%d comment was successfully marked as published.",
                ),
                _(
                    "%d comments were successfully marked as published.",
                ),
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    def make_unpublished(self, request, queryset):
        updated = queryset.update(status="False")
        self.message_user(
            request,
            ngettext(
                _(
                    "%d comment was successfully marked as published.",
                ),
                _(
                    "%d comments were successfully marked as published.",
                ),
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    setattr(
        make_published,
        "short_description",
        _("Mark selected comments as published"),
    )

    setattr(
        make_unpublished,
        "short_description",
        _("Mark selected comments as unpublished"),
    )
