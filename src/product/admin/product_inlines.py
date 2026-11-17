from django.contrib import admin
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.admin import TabularInline
from unfold.decorators import display

from admin.base import (
    BaseTranslatableTabularInline,
)
from admin.displays import (
    format_dt,
)
from product.admin.variants import STOCK_RESERVATION_STATUS_VARIANT
from product.models.highlight import ProductHighlight
from product.models.image import ProductImage
from product.models.price_spell import ProductPriceSpell
from product.models.product_attribute import ProductAttribute
from product.models.relation import ProductRelation


class ProductAttributeInline(TabularInline):
    """Inline for managing product attributes within product admin."""

    model = ProductAttribute
    extra = 1
    fields = ("attribute_value",)
    autocomplete_fields = ["attribute_value"]

    tab = True
    per_page = 20
    # Paged, so it needs a fixed order: unordered pages can repeat a row.
    ordering = ("pk",)
    verbose_name = _("Product Attribute")
    verbose_name_plural = _("Product Attributes")

    def get_queryset(self, request):
        """Optimize queryset with related data."""
        qs = super().get_queryset(request)
        return qs.select_related("attribute_value__attribute").prefetch_related(
            "attribute_value__translations",
            "attribute_value__attribute__translations",
        )


class ProductImageInline(BaseTranslatableTabularInline):
    # Unfold's inline image widget previews the current file itself. The
    # alt text is per language: parler shows the language tab of the
    # product page.
    model = ProductImage
    extra = 0
    fields = ("image", "is_main", "alt_text")

    tab = True
    # No ``per_page``: Unfold's paged formset and parler's translated one
    # cannot be combined (it passes ``request`` to a formset that has no
    # such argument).
    show_change_link = True


class ProductHighlightInline(BaseTranslatableTabularInline):
    """The product's Google Merchant highlights: short benefits, one row
    each, written per language (the language tab of the product page) and
    dragged into the order they are published in."""

    model = ProductHighlight
    extra = 0
    fields = ("text", "sort_order")
    tab = True
    # Dragging a row sets its ``sort_order``.
    ordering_field = "sort_order"
    hide_ordering_field = True
    # No ``per_page``: Unfold's paged formset and parler's translated one
    # cannot be combined (see ``ProductImageInline``).


class ProductPriceSpellInline(TabularInline):
    """The product's price record, newest first. Read-only: it is the
    evidence behind every announced reduction, so nothing here is edited
    or deleted by hand."""

    model = ProductPriceSpell
    extra = 0
    fields = ("price", "valid_from", "valid_to")
    readonly_fields = fields
    ordering = ("-valid_from",)
    tab = True
    per_page = 20

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class StockReservationInline(TabularInline):
    """Display active stock reservations for this product."""

    from order.models.stock_reservation import StockReservation

    model = StockReservation
    extra = 0
    can_delete = False

    fields = (
        "reservation_info",
        "quantity",
        "session_info",
        "expires_display",
        "status_display",
    )
    readonly_fields = fields

    tab = True
    per_page = 20
    verbose_name = _("Active Stock Reservation")
    verbose_name_plural = _("Active Stock Reservations")

    def get_queryset(self, request):
        """Only show active (non-expired, non-consumed) reservations."""
        qs = super().get_queryset(request)
        now = timezone.now()
        return (
            qs.filter(expires_at__gt=now, consumed=False)
            .select_related("reserved_by", "order")
            .order_by("-created_at")
        )

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Reservation"))
    def reservation_info(self, obj):
        return f"#{obj.id} — {format_dt(obj.created_at)}"

    @admin.display(description=_("Reserved By"))
    def session_info(self, obj):
        if obj.reserved_by:
            return obj.reserved_by.email or obj.reserved_by.username
        session_short = (
            obj.session_id[:8] if len(obj.session_id) > 8 else obj.session_id
        )
        return _("Guest: %(session)s…") % {"session": session_short}

    @admin.display(description=_("Expires In"))
    def expires_display(self, obj):
        minutes_left = int(
            (obj.expires_at - timezone.now()).total_seconds() / 60
        )
        return _("%(minutes)s min") % {"minutes": minutes_left}

    @display(description=_("Status"), label=STOCK_RESERVATION_STATUS_VARIANT)
    def status_display(self, obj):
        if obj.order_id:
            return "consumed", _("Order #%(id)s") % {"id": obj.order_id}
        return "pending", _("Pending checkout")


class ProductRelationInline(TabularInline):
    """Merchant-curated related products — the Free tier of the
    recommendation engine (docs/recommendations-engine.md §2.1).

    ``fk_name`` is required: ``ProductRelation`` has two FKs to
    ``Product`` and Django cannot pick one (admin.E202). Drag-and-drop
    ordering is per source product (``ProductRelation
    .get_ordering_queryset``), which is what the merchant is arranging
    here.
    """

    model = ProductRelation
    fk_name = "from_product"
    fields = ("to_product", "relation_type", "sort_order")
    autocomplete_fields = ["to_product"]
    ordering_field = "sort_order"
    hide_ordering_field = True
    extra = 0
    tab = True
    verbose_name = _("Related product")
    verbose_name_plural = _("Related products")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("to_product")
