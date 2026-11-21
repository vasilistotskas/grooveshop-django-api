from datetime import timedelta

from django.contrib import admin, messages
from django.db.models import Count, Prefetch, Sum
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext
from parler.admin import TranslatableAdmin
from simple_history.admin import SimpleHistoryAdmin
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
    RangeNumericFilter,
    SliderNumericFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from admin.base import (
    BaseModelAdmin,
)
from admin.bulk import update_each
from admin.displays import (
    format_dt,
    header_two_line,
    money,
    relative_time,
)
from admin.export import ExportActionMixin
from admin.features import Gate, Kind
from admin.filters import (
    LikesCountFilter,
    RelatedDropdownFilter,
)
from core.forms.measurement import MeasurementWidget
from core.measurement import DistanceUnits, WeightUnits
from product.admin.filters import (
    DiscountStatusFilter,
    PopularityFilter,
    PriceRangeFilter,
    ReviewAverageFilter,
    StockReservationStatusFilter,
    StockStatusFilter,
)
from product.admin.product_inlines import (
    ProductAttributeInline,
    ProductHighlightInline,
    ProductImageInline,
    ProductPriceSpellInline,
    ProductRelationInline,
    StockReservationInline,
)
from product.admin.stock_history import (
    StockHistoryAdminMixin,
    StockLogDataset,
)
from product.admin.variants import STOCK_STATUS_VARIANT
from product.forms import ApplyDiscountForm
from product.models.image import ProductImage
from product.models.product import (
    COST_PRICE_FIELDS,
    COST_PRICE_PERMISSION,
    Product,
)
from product.services import ProductDuplicationError, ProductService
from tag.admin import TaggedItemInline


@admin.register(Product)
class ProductAdmin(
    StockHistoryAdminMixin,
    TranslatableAdmin,
    ExportActionMixin,
    SimpleHistoryAdmin,
    BaseModelAdmin,
):
    """Parler owns forms/urls (first in the MRO, matching
    ``BaseTranslatableAdmin``'s contract). ``ExportActionMixin`` only
    adds CSV/XML export actions (no cooperative-``super()`` methods
    of its own) and ``SimpleHistoryAdmin`` adds the audit-history
    view — neither redefines unfold's plumbing, so both sit between
    parler and ``BaseModelAdmin`` without breaking either chain.
    ``BaseTranslatableAdmin`` cannot express that order (its bases are
    parler then ``BaseModelAdmin``), which is why this one admin lists
    the bases itself.
    """

    # A feature that is off keeps its surface off the product form: its
    # setup (points, the alert switch) for those who can switch it on,
    # what shoppers produced (likes) only while there is some.
    feature_gates = (
        Gate(
            "loyalty",
            fields=("points_coefficient", "points"),
            kind=Kind.SETUP,
        ),
        Gate(
            "product_alerts",
            fields=("price_drop_alerts_enabled",),
            filters=("price_drop_alerts_enabled",),
            kind=Kind.SETUP,
        ),
        Gate(
            "favourites",
            fields=("likes_count",),
            filters=(LikesCountFilter,),
        ),
        Gate("product_reviews", filters=(ReviewAverageFilter,)),
    )

    prefetch_translations = ("category__translations",)
    list_display = (
        "product_info",
        "category_display",
        "variant_group_display",
        "pricing_info",
        "stock_status",
        "active",
        "created_display",
    )
    search_fields = [
        "id",
        "sku",
        "external_reference",
        "translations__name",
        "translations__description",
        "category__translations__name",
        "brand__name",
    ]
    list_filter = [
        StockStatusFilter,
        StockReservationStatusFilter,
        PriceRangeFilter,
        DiscountStatusFilter,
        PopularityFilter,
        "active",
        ("category", RelatedDropdownFilter),
        ("variant_group", RelatedDropdownFilter),
        ("brand", RelatedDropdownFilter),
        ("vat", RelatedDropdownFilter),
        ("stock", RangeNumericFilter),
        ("price", RangeNumericFilter),
        ("discount_percent", SliderNumericFilter),
        ("view_count", RangeNumericFilter),
        ("created_at", RangeDateTimeFilter),
        ("updated_at", RangeDateTimeFilter),
        LikesCountFilter,
        ReviewAverageFilter,
        "price_drop_alerts_enabled",
    ]
    inlines = [
        ProductAttributeInline,
        ProductImageInline,
        ProductHighlightInline,
        StockReservationInline,
        ProductPriceSpellInline,
        TaggedItemInline,
        ProductRelationInline,
    ]
    change_form_datasets = [StockLogDataset]
    readonly_fields = (
        "id",
        "uuid",
        "sku",
        "created_at",
        "updated_at",
        "view_count",
        "likes_count",
        "stock_reservation_summary",
        # Derived by ``product.pricing.service``; shown, never edited.
        "effective_price",
        "prior_price",
        "reduction_started_at",
        # Stamped from the saving admin (``Product._history_user``).
        "changed_by",
    )
    list_select_related = ["category", "vat", "brand"]
    autocomplete_fields = ["category", "vat", "variant_group", "brand"]
    search_help_text = _(
        "Search by ID, SKU, name, description, or category name."
    )
    actions = [
        "make_active",
        "make_inactive",
        "apply_custom_discount",
        "clear_discount",
        "export_csv",
        "export_xml",
    ]
    # Per-row quick action: clone a product into a new draft for the
    # catalog team to riff on without leaving the list page.
    actions_row = ["duplicate_product_row"]
    date_hierarchy = "created_at"

    fieldsets = (
        (
            _("Product Information"),
            {
                "fields": (
                    "sku",
                    "external_reference",
                    "category",
                    "variant_group",
                    "brand",
                    "gtin",
                    "mpn",
                    "active",
                    "name",
                    "feed_title",
                    "description",
                ),
                "classes": ("tab",),
                "description": _(
                    "Assign a variant group to link this product with its "
                    "sibling variations (e.g. other colours). Flag the relevant "
                    "attributes (Colour, Memory) as variant axes under "
                    "Attributes for them to render as storefront selectors."
                ),
            },
        ),
        (
            _("Pricing & Inventory"),
            {
                "fields": (
                    "price",
                    "discount_percent",
                    "vat",
                    "effective_price",
                    "prior_price",
                    "reduction_started_at",
                    "stock",
                    "weight",
                    "length",
                    "width",
                    "height",
                    "low_stock_threshold",
                ),
                "classes": ("tab",),
            },
        ),
        (
            _("Loyalty Points"),
            {
                "fields": (
                    "points_coefficient",
                    "points",
                ),
                "classes": ("tab",),
                "description": _(
                    "Configure loyalty points earning for this product. "
                    "Points coefficient multiplies the global points factor, "
                    "and bonus points are awarded as a fixed amount on purchase."
                ),
            },
        ),
        (
            _("Customer Alerts"),
            {
                "fields": ("price_drop_alerts_enabled",),
                "classes": ("tab",),
                "description": _(
                    "Control which self-service alert features customers "
                    "can subscribe to for this product. Disabled by default "
                    "— admins opt products in per SKU."
                ),
            },
        ),
        (
            _("Stock Audit"),
            {
                "fields": ("stock_reservation_summary",),
                "classes": ("tab",),
                "description": _(
                    "View active stock reservations and recent stock operations for this product."
                ),
            },
        ),
        (
            _("SEO"),
            {
                "fields": (
                    "slug",
                    "seo_title",
                    "seo_description",
                    "seo_keywords",
                ),
                "classes": ("tab",),
            },
        ),
        (
            _("Performance"),
            {
                "fields": ("view_count", "likes_count"),
                "classes": ("tab",),
            },
        ),
        (
            _("System Information"),
            {
                "fields": (
                    "id",
                    "uuid",
                    "created_at",
                    "updated_at",
                    "changed_by",
                ),
                "classes": ("tab",),
            },
        ),
    )

    # The cost price never reaches a file an ADMIN can download.
    export_exclude_fields = ExportActionMixin.export_exclude_fields | set(
        COST_PRICE_FIELDS
    )

    def get_fieldsets(self, request, obj=None):
        """The cost price sits under the price, for the OWNER only.

        The field is left out of the form altogether for everyone else, so a
        crafted POST cannot write it either.
        """
        fieldsets = super().get_fieldsets(request, obj)
        if not request.user.has_perm(COST_PRICE_PERMISSION):
            return fieldsets
        return [
            (
                title,
                {
                    **options,
                    "fields": tuple(
                        name
                        for field in options["fields"]
                        for name in (
                            (field, "cost_price")
                            if field == "price"
                            else (field,)
                        )
                    ),
                },
            )
            for title, options in fieldsets
        ]

    def get_form(self, request, obj=None, change=False, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields["weight"].widget = MeasurementWidget(
            unit_choices=WeightUnits.CHOICES
        )
        for name in ("length", "width", "height"):
            form.base_fields[name].widget = MeasurementWidget(
                unit_choices=DistanceUnits.CHOICES
            )
        return form

    def get_queryset(self, request):
        # Only apply expensive annotations when filtering/sorting requires
        # them. The ``else`` branches used to annotate unconditionally
        # "to avoid attribute errors", but that fired both joins
        # (``LEFT JOIN productfavourite`` + ``LEFT JOIN productreview``
        # + ``GROUP BY``) on every changelist load — a cartesian product
        # of favourites × reviews per product, costing 2.4s on the
        # main query and 875ms on the date-hierarchy DATE_TRUNC.
        # ``Product.likes_count`` / ``review_average`` properties
        # already fall back to per-row queries when no annotation is
        # present, so removing the unconditional annotation just shifts
        # the cost from "2.4s once" to "~1ms × 25 rows = ~25ms". These
        # two properties are no longer rendered as a list_display column
        # at all (dropped along with ``performance_metrics`` — see
        # ``PopularityFilter``/the "Performance" fieldset for where
        # views/likes/rating are still surfaced), but the conditional
        # annotation stays for the filter/ordering paths above.
        qs = super().get_queryset(request)

        # Check if we need likes_count or review_average based on filters/ordering
        needs_likes = (
            any("likes_count" in str(param) for param in request.GET)
            or request.GET.get("o", "").find("likes") != -1
        )

        needs_reviews = (
            any("review_average" in str(param) for param in request.GET)
            or request.GET.get("o", "").find("review") != -1
        )

        if needs_likes:
            qs = qs.with_likes_count()

        if needs_reviews:
            qs = qs.with_review_average()

        # Optimize related queries
        main_images = Prefetch(
            "images",
            queryset=ProductImage.objects.filter(is_main=True).only(
                "id", "product_id", "image", "is_main"
            ),
            to_attr="main_images_list",
        )

        # Sibling ids per group via prefetch (one cheap extra query) instead
        # of a Count() annotation — an unconditional multi-valued join here
        # would cartesian with the conditional likes/reviews annotations
        # above, the exact blowup this method exists to avoid.
        variant_siblings = Prefetch(
            "variant_group__variants",
            queryset=Product.objects.only("id", "variant_group_id"),
        )

        qs = qs.select_related(
            "category", "vat", "variant_group"
        ).prefetch_related(
            main_images,
            variant_siblings,
            "variant_group__translations",
        )
        if not self.serves_changelist(request):
            # The change form edits and saves the whole row.
            return qs
        return qs.only(
            # Only load fields the list displays
            "id",
            "sku",
            "active",
            "price",
            "price_currency",
            "discount_percent",
            "stock",
            "low_stock_threshold",
            "view_count",
            "created_at",
            "updated_at",
            "category_id",
            "vat_id",
            "changed_by_id",
            "variant_group_id",
        )

    def get_prepopulated_fields(self, request, obj=None):
        return {"slug": ("name",)}

    @display(
        description=_("Product"), header=True, ordering="translations__name"
    )
    def product_info(self, obj):
        name = obj.safe_translation_getter("name", any_language=True) or _(
            "Untitled Product"
        )
        main_images = getattr(obj, "main_images_list", [])
        main_image = main_images[0] if main_images else None
        image_path = (
            main_image.image.url if main_image and main_image.image else None
        )
        return header_two_line(
            name,
            _("SKU %(sku)s") % {"sku": obj.sku[:8]},
            image_path=image_path,
        )

    @admin.display(
        description=_("Category"), ordering="category__translations__name"
    )
    def category_display(self, obj):
        if not obj.category:
            return "—"
        return obj.category.safe_translation_getter(
            "name", any_language=True
        ) or _("Unnamed")

    @admin.display(description=_("Variants"))
    def variant_group_display(self, obj):
        """Which variant family this product belongs to, linked to the
        group page, with the sibling count. Reads only prefetched data."""
        group = obj.variant_group
        if group is None:
            return "—"
        name = group.safe_translation_getter("name", any_language=True) or _(
            "Group #%(id)s"
        ) % {"id": group.pk}
        # len() over the prefetch cache — no per-row COUNT query.
        siblings = len(group.variants.all())
        return format_html(
            '<a href="{url}">{name}</a> ({count} {label})',
            url=reverse(
                "admin:product_productvariantgroup_change", args=[group.pk]
            ),
            name=name,
            count=siblings,
            label=_("variants"),
        )

    @admin.display(description=_("Pricing"), ordering="price")
    def pricing_info(self, obj):
        if obj.discount_percent > 0:
            return _("%(price)s -> %(final)s (-%(discount)s%%)") % {
                "price": money(obj.price.amount),
                "final": money(obj.final_price.amount),
                "discount": obj.discount_percent,
            }
        return money(obj.price.amount)

    @display(
        description=_("Stock"), label=STOCK_STATUS_VARIANT, ordering="stock"
    )
    def stock_status(self, obj):
        state = obj.stock_state
        return state, _("%(state)s (%(stock)s)") % {
            "state": state.label,
            "stock": obj.stock,
        }

    @display(description=_("Created"), ordering="created_at")
    def created_display(self, obj):
        return (
            f"{format_dt(obj.created_at, fmt='SHORT_DATE_FORMAT')} "
            f"({relative_time(obj.created_at)})"
        )

    @admin.display(description=_("Stock Reservation Summary"))
    def stock_reservation_summary(self, obj):
        """Active reservations + last-7-days stock ops, with a link to
        the full stock-history chart (``stock_history_view``)."""
        from order.models.stock_log import StockLog
        from order.models.stock_reservation import StockReservation

        now = timezone.now()

        active_reservations = StockReservation.objects.active().filter(
            product=obj
        )
        reservation_count = active_reservations.count()
        reserved_qty = (
            active_reservations.aggregate(total=Sum("quantity"))["total"] or 0
        )
        available_stock = obj.stock - reserved_qty

        seven_days_ago = now - timedelta(days=7)
        ops_by_type = dict(
            StockLog.objects.filter(product=obj, created_at__gte=seven_days_ago)
            .values_list("operation_type")
            .annotate(count=Count("id"))
        )

        history_url = reverse(
            "admin:product_product_stock_history", args=[obj.pk]
        )

        return format_html(
            "<p>{stock_line}</p><p>{ops_line}</p>"
            '<p><a href="{url}">{label}</a></p>',
            stock_line=_(
                "Stock %(stock)s — reserved %(reserved)s, available "
                "%(available)s across %(count)s active reservation(s)."
            )
            % {
                "stock": obj.stock,
                "reserved": reserved_qty,
                "available": available_stock,
                "count": reservation_count,
            },
            ops_line=_(
                "Last 7 days: %(reserve)s reserved, %(release)s released, "
                "%(decrement)s decremented, %(increment)s incremented."
            )
            % {
                "reserve": ops_by_type.get("RESERVE", 0),
                "release": ops_by_type.get("RELEASE", 0),
                "decrement": ops_by_type.get("DECREMENT", 0),
                "increment": ops_by_type.get("INCREMENT", 0),
            },
            url=history_url,
            label=_("Open full stock history chart"),
        )

    @action(
        description=_("Activate selected products"),
        permissions=["change"],
        variant=ActionVariant.SUCCESS,
        icon="check_circle",
    )
    def make_active(self, request, queryset):
        # Through `save`, so the post_save receivers run: the Meilisearch
        # documents keep their `active` value and the storefront cache
        # surfaces are marked stale.
        updated = update_each(queryset, active=True)
        self.message_user(
            request,
            ngettext(
                "%(count)d product was successfully activated.",
                "%(count)d products were successfully activated.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=_("Deactivate selected products"),
        permissions=["change"],
        variant=ActionVariant.WARNING,
        icon="cancel",
    )
    def make_inactive(self, request, queryset):
        # Through `save`, so the post_save receivers run: the Meilisearch
        # documents keep their `active` value and the storefront cache
        # surfaces are marked stale.
        updated = update_each(queryset, active=False)
        self.message_user(
            request,
            ngettext(
                "%(count)d product was successfully deactivated.",
                "%(count)d products were successfully deactivated.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=_("Apply custom discount to selected products"),
        variant=ActionVariant.INFO,
        icon="local_offer",
        permissions=["change"],
    )
    def apply_custom_discount(self, request, queryset):
        """Ask for the percentage, then apply it to the selection.

        A bulk action over a selection cannot open an Unfold dialog, so
        this is Django's intermediate-page pattern (``delete_selected``):
        the page re-posts the action and the selection, and Django hands
        the action the queryset built from that POST. Never the session
        — it is shared across tabs, and a selection stored there by one
        tab was discounted from another.
        """
        form = ApplyDiscountForm(
            request.POST if "discount_percent" in request.POST else None
        )
        if not form.is_valid():
            active_count = queryset.filter(active=True).count()
            total_count = queryset.count()
            return TemplateResponse(
                request,
                "admin/product/apply_discount.html",
                {
                    **self.admin_site.each_context(request),
                    "title": _("Apply Custom Discount"),
                    "opts": self.model._meta,
                    "form": form,
                    "queryset": queryset,
                    "total_count": total_count,
                    "active_count": active_count,
                    "inactive_count": total_count - active_count,
                },
            )

        discount_percent = form.cleaned_data["discount_percent"]
        if not form.cleaned_data["apply_to_inactive"]:
            queryset = queryset.filter(active=True)

        updated = ProductService.set_discount(queryset, discount_percent)

        self.message_user(
            request,
            ngettext(
                "Applied %(discount)s%% discount to %(count)d product.",
                "Applied %(discount)s%% discount to %(count)d products.",
                updated,
            )
            % {"count": updated, "discount": discount_percent},
            messages.SUCCESS,
        )
        # None: Django returns to the changelist, filters kept.
        return None

    @action(
        description=_("Clear discount from selected products"),
        permissions=["change"],
        variant=ActionVariant.DANGER,
        icon="cancel",
    )
    def clear_discount(self, request, queryset):
        updated = ProductService.clear_discount(queryset)
        self.message_user(
            request,
            ngettext(
                "%(count)d product's discount was cleared.",
                "%(count)d products' discounts were cleared.",
                updated,
            )
            % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=_("Duplicate as draft"),
        permissions=["add"],
        icon="content_copy",
        variant=ActionVariant.INFO,
    )
    def duplicate_product_row(self, request, object_id):
        """Clone the row into an inactive draft and open it for editing
        (``ProductService.duplicate_as_draft`` holds the rules)."""

        try:
            original = Product.objects.get(pk=object_id)
        except Product.DoesNotExist:
            messages.error(request, _("Product not found."))
            return redirect("admin:product_product_changelist")

        try:
            clone = ProductService.duplicate_as_draft(original)
        except ProductDuplicationError:
            messages.error(
                request,
                _("Couldn't find a free slug — try renaming the source."),
            )
            return redirect("admin:product_product_change", object_id=object_id)

        messages.success(
            request,
            _(
                "Cloned product #%(orig)s to draft #%(clone)s. Edit the copy "
                "and re-activate when ready."
            )
            % {"orig": original.id, "clone": clone.id},
        )
        return redirect("admin:product_product_change", object_id=clone.id)

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<int:product_id>/stock-history/",
                self.admin_site.admin_view(self.stock_history_view),
                name="product_product_stock_history",
            ),
        ]
        return custom_urls + urls
