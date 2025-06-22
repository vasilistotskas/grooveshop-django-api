import admin_thumbnails
from django.contrib import admin, messages
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from mptt.admin import DraggableMPTTAdmin
from parler.admin import TranslatableAdmin, TranslatableTabularInline
from simple_history.admin import SimpleHistoryAdmin
from unfold.admin import ModelAdmin
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
    RangeNumericFilter,
    RangeNumericListFilter,
    RelatedDropdownFilter,
    SliderNumericFilter,
)
from unfold.decorators import action
from unfold.enums import ActionVariant

from core.admin import ExportModelAdmin
from core.fields.measurement import MeasurementField
from core.forms.measurement import MeasurementWidget
from core.units import WeightUnits
from product.models.category import ProductCategory
from product.models.category_image import ProductCategoryImage
from product.models.favourite import ProductFavourite
from product.models.image import ProductImage
from product.models.product import Product
from product.models.review import ProductReview
from tag.admin import TagInLine


def category_update_action(category):
    def category_update(model_admin, request, queryset):
        return queryset.update(category=category)

    category_update.__name__ = "make_action_{}".format(category.name)
    category_update.short_description = (
        _("Update category to %s") % category.name
    )
    return category_update


@admin_thumbnails.thumbnail("image")
class ProductCategoryImageInline(TranslatableTabularInline):
    model = ProductCategoryImage
    readonly_fields = ("id",)
    extra = 1
    fields = ("image", "image_type", "active", "sort_order", "translations")


@admin.register(ProductCategory)
class CategoryAdmin(ModelAdmin, TranslatableAdmin, DraggableMPTTAdmin):
    mptt_indent_field = "translations__name"
    list_per_page = 10
    list_display = (
        "tree_actions",
        "indented_title",
        "related_products_count",
        "related_products_cumulative_count",
    )
    list_display_links = ("indented_title",)
    search_fields = ("translations__name",)
    inlines = [ProductCategoryImageInline]

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
        return qs

    def get_prepopulated_fields(self, request, obj=None):
        return {
            "slug": ("name",),
        }

    def related_products_count(self, instance):
        return instance.products_count

    related_products_count.short_description = _(
        "Related products (for this specific category)"
    )

    def related_products_cumulative_count(self, instance):
        return instance.products_cumulative_count

    related_products_cumulative_count.short_description = _(
        "Related products (in tree)"
    )


@admin.register(ProductFavourite)
class FavouriteAdmin(ModelAdmin):
    list_display = ["user", "product"]


@admin_thumbnails.thumbnail("image")
class ProductImageInline(TranslatableTabularInline):
    model = ProductImage
    readonly_fields = ("id",)
    extra = 1


@admin.register(ProductCategoryImage)
class ProductCategoryImageAdmin(ModelAdmin, TranslatableAdmin):
    list_display = [
        "id",
        "category_name",
        "image_type",
        "active",
        "sort_order",
        "created_at",
    ]
    list_filter = [
        "image_type",
        "active",
        ("category", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    ]
    search_fields = [
        "category__translations__name",
        "translations__title",
        "translations__alt_text",
    ]
    list_select_related = ["category"]
    readonly_fields = ("created_at", "updated_at", "uuid")
    ordering = ["category", "image_type", "sort_order"]

    def category_name(self, obj):
        return obj.category.safe_translation_getter("name", any_language=True)

    category_name.short_description = _("Category")
    category_name.admin_order_field = "category__translations__name"


class LikesCountFilter(RangeNumericListFilter):
    title = _("Likes count")
    parameter_name = "likes_count"

    def queryset(self, request, queryset):
        filters = {}

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["likes_count_field__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["likes_count_field__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


class ReviewAverageFilter(RangeNumericListFilter):
    title = _("Review Rating")
    parameter_name = "review_average"

    def queryset(self, request, queryset):
        filters = {}

        value_from = self.used_parameters.get(f"{self.parameter_name}_from")
        if value_from and value_from != "":
            filters["review_average_field__gte"] = value_from

        value_to = self.used_parameters.get(f"{self.parameter_name}_to")
        if value_to and value_to != "":
            filters["review_average_field__lte"] = value_to

        return queryset.filter(**filters) if filters else queryset

    def expected_parameters(self):
        return [
            f"{self.parameter_name}_from",
            f"{self.parameter_name}_to",
        ]


@admin.register(Product)
class ProductAdmin(
    TranslatableAdmin, ExportModelAdmin, SimpleHistoryAdmin, ModelAdmin
):
    list_display = [
        "id",
        "product_name",
        "category",
        "price",
        "display_final_price",
        "display_discount_value",
        "display_price_save_percent",
        "stock",
        "colored_stock",
        "boolean_status",
        "display_likes_count",
        "display_review_average",
    ]
    search_fields = ["id", "product_code", "translations__name"]
    list_filter = [
        "active",
        ("category", RelatedDropdownFilter),
        ("stock", RangeNumericFilter),
        ("price", RangeNumericFilter),
        ("discount_percent", SliderNumericFilter),
        ("view_count", RangeNumericFilter),
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
        LikesCountFilter,
        ReviewAverageFilter,
    ]
    inlines = [ProductImageInline, TagInLine]
    readonly_fields = (
        "likes_count",
        "display_final_price",
        "display_discount_value",
        "display_price_save_percent",
        "display_review_average",
    )
    list_select_related = ["category", "vat"]
    list_per_page = 25
    actions = [
        "make_active",
        "make_inactive",
        "clear_discount",
    ]
    list_filter_submit = True
    list_filter_sheet = True

    formfield_overrides = {
        MeasurementField: {
            "widget": MeasurementWidget(
                unit_choices=WeightUnits.CHOICES,
            )
        },
    }

    def get_queryset(self, request):
        return super().get_queryset(request).with_all_annotations()

    def get_prepopulated_fields(self, request, obj=None):
        return {
            "slug": ("name",),
        }

    def boolean_status(self, obj):
        return bool(obj.active)

    boolean_status.boolean = True
    boolean_status.short_description = _("Active")

    def product_name(self, obj):
        return (
            obj.safe_translation_getter("name", any_language=True) or "Untitled"
        )

    product_name.short_description = _("Name")
    product_name.admin_order_field = "translations__name"

    def display_final_price(self, obj):
        return obj.final_price

    display_final_price.short_description = _("Final Price")
    display_final_price.admin_order_field = "final_price_amount"

    def display_discount_value(self, obj):
        return obj.discount_value

    display_discount_value.short_description = _("Discount Value")
    display_discount_value.admin_order_field = "discount_value_amount"

    def display_price_save_percent(self, obj):
        return f"{obj.price_save_percent:.2f}%"

    display_price_save_percent.short_description = _("Saving %")
    display_price_save_percent.admin_order_field = "price_save_percent_field"

    def display_likes_count(self, obj):
        return obj.likes_count

    display_likes_count.short_description = _("Likes Count")
    display_likes_count.admin_order_field = "likes_count_field"

    def display_review_average(self, obj):
        return f"{obj.review_average:.1f}/10"

    display_review_average.short_description = _("Rating")
    display_review_average.admin_order_field = "review_average_field"

    @action(
        description=_("Activate selected products"),
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def make_active(self, request, queryset):
        updated = queryset.update(active=True)
        self.message_user(
            request,
            ngettext(
                _("%d product was successfully activated."),
                _("%d products were successfully activated."),
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @action(
        description=_("Deactivate selected products"),
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def make_inactive(self, request, queryset):
        updated = queryset.update(active=False)
        self.message_user(
            request,
            ngettext(
                _("%d product was successfully deactivated."),
                _("%d products were successfully deactivated."),
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    @action(
        description=_("Clear discount from selected products"),
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def clear_discount(self, request, queryset):
        updated = queryset.update(discount_percent=0.0)
        self.message_user(
            request,
            ngettext(
                _("%d product's discount was cleared."),
                _("%d products' discounts were cleared."),
                updated,
            )
            % updated,
            messages.SUCCESS,
        )


@admin.register(ProductReview)
class ReviewAdmin(ModelAdmin, TranslatableAdmin):
    list_display = [
        "comment",
        "status",
        "created_at",
        "product_link",
        "user_link",
    ]
    list_filter = ["status", ("created_at", RangeDateTimeFilter)]
    actions = ["make_published", "make_unpublished"]
    search_fields = [
        "translations__comment",
        "user__email",
        "user__username",
        "product__translations__name",
    ]
    list_select_related = ["product", "user"]
    list_filter_submit = True

    def product_link(self, obj):
        if obj.product:
            name = obj.product.safe_translation_getter(
                "name", any_language=True
            ) or str(obj.product.id)
            url = f"/admin/product/product/{obj.product.id}/change/"
            return format_html('<a href="{}">{}</a>', url, name)
        return "-"

    product_link.short_description = _("Product")

    def user_link(self, obj):
        if obj.user:
            url = f"/admin/user/useraccount/{obj.user.id}/change/"
            return format_html('<a href="{}">{}</a>', url, obj.user.email)
        return "-"

    user_link.short_description = _("User")

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
                    "%d comment was successfully marked as unpublished.",
                ),
                _(
                    "%d comments were successfully marked as unpublished.",
                ),
                updated,
            )
            % updated,
            messages.SUCCESS,
        )

    make_published.short_description = _("Mark selected comments as published")

    make_unpublished.short_description = _(
        "Mark selected comments as unpublished"
    )
