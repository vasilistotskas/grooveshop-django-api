from datetime import timedelta

from django.db.models import F
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    DropdownFilter,
)

from admin.filters import (
    AnnotatedRangeFilter,
)
from product.enum.review import ReviewStatus
from product.enum.stock import StockState


class StockStatusFilter(DropdownFilter):
    title = _("Stock Status")
    parameter_name = "stock_status"

    def lookups(self, request, model_admin):
        return StockState.choices

    def queryset(self, request, queryset):
        value = self.value()
        if value not in StockState.values:
            return queryset
        return queryset.by_stock_state(StockState(value))


class PriceRangeFilter(DropdownFilter):
    title = _("Price Range")
    parameter_name = "price_range"

    def lookups(self, request, model_admin):
        return [
            ("budget", _("Budget (€0-€20)")),
            ("affordable", _("Affordable (€20-€50)")),
            ("mid_range", _("Mid-range (€50-€100)")),
            ("premium", _("Premium (€100-€500)")),
            ("luxury", _("Luxury (€500+)")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()

        match filter_value:
            case "budget":
                filter_kwargs = {"price__lte": 20}
            case "affordable":
                filter_kwargs = {"price__range": (20, 50)}
            case "mid_range":
                filter_kwargs = {"price__range": (50, 100)}
            case "premium":
                filter_kwargs = {"price__range": (100, 500)}
            case "luxury":
                filter_kwargs = {"price__gt": 500}
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class DiscountStatusFilter(DropdownFilter):
    title = _("Discount Status")
    parameter_name = "discount_status"

    def lookups(self, request, model_admin):
        return [
            ("on_sale", _("On Sale (>0%)")),
            ("heavy_discount", _("Heavy Discount (>20%)")),
            ("clearance", _("Clearance (>50%)")),
            ("no_discount", _("No Discount (0%)")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()

        match filter_value:
            case "on_sale":
                filter_kwargs = {"discount_percent__gt": 0}
            case "heavy_discount":
                filter_kwargs = {"discount_percent__gt": 20}
            case "clearance":
                filter_kwargs = {"discount_percent__gt": 50}
            case "no_discount":
                filter_kwargs = {"discount_percent": 0}
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class PopularityFilter(DropdownFilter):
    title = _("Popularity")
    parameter_name = "popularity"

    def lookups(self, request, model_admin):
        return [
            ("trending", _("Trending (High Views)")),
            ("loved", _("Loved (High Likes)")),
            # 7.0, not 4.0. `RateEnum` is 1-10 and `rating_display`
            # renders "x/10", so both numbers are on the same scale and
            # the label was simply wrong: an admin picking ">4.0" lost
            # every product averaging 4.1-7.0 without being told.
            ("well_reviewed", _("Well Reviewed (>7.0)")),
            ("new_arrivals", _("New Arrivals (Last 30 days)")),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()

        match filter_value:
            case "trending":
                filter_kwargs = {"view_count__gt": 100}
            case "loved":
                queryset = queryset.with_likes_count()
                filter_kwargs = {"likes_count__gt": 10}
            case "well_reviewed":
                queryset = queryset.with_review_average()
                filter_kwargs = {"review_average__gt": 7.0}
            case "new_arrivals":
                thirty_days_ago = timezone.now() - timedelta(days=30)
                filter_kwargs = {"created_at__gte": thirty_days_ago}
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


class ReviewAverageFilter(AnnotatedRangeFilter):
    title = _("Review Rating")
    parameter_name = "review_average"

    def annotate(self, queryset):
        return queryset.with_review_average()


class StockReservationStatusFilter(DropdownFilter):
    """Filter products by stock reservation status."""

    title = _("Reservation Status")
    parameter_name = "reservation_status"

    def lookups(self, request, model_admin):
        return [
            ("has_reservations", _("Has Active Reservations")),
            ("no_reservations", _("No Active Reservations")),
            # xgettext:no-python-format — "% s" here is a literal percent.
            ("high_reservations", _("High Reservations (>50% stock)")),
        ]

    def queryset(self, request, queryset):
        match self.value():
            case "has_reservations":
                return queryset.with_active_reserved_quantity().filter(
                    active_reserved_quantity__gt=0
                )
            case "no_reservations":
                return queryset.with_active_reserved_quantity().filter(
                    active_reserved_quantity=0
                )
            case "high_reservations":
                return queryset.with_active_reserved_quantity().filter(
                    active_reserved_quantity__gt=F("stock") * 0.5
                )

        return queryset


class ReviewStatusFilter(DropdownFilter):
    """Opens on the reviews waiting for approval.

    Same query parameter as Django's own ``status`` filter, so the
    dashboard's "Reviews to approve" link (``status__exact=NEW``) keeps
    working. The unfiltered list is an explicit choice, because with the
    default being a value, "no parameter" can no longer mean "all".
    """

    title = _("Status")
    parameter_name = "status__exact"
    all_option = None
    ALL = "all"

    def lookups(self, request, model_admin):
        return [
            *ReviewStatus.choices,
            (self.ALL, _("All")),
        ]

    def value(self):
        return super().value() or ReviewStatus.NEW

    def queryset(self, request, queryset):
        if self.value() == self.ALL:
            return queryset
        return queryset.filter(status=self.value())
