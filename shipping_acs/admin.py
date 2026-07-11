"""Django admin for ACS shipping models — uses unfold for theming.

Mirrors :mod:`shipping_boxnow.admin` so support flows are consistent
across providers.
"""

from __future__ import annotations

import logging

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateFilter,
    RangeDateTimeFilter,
)
from unfold.decorators import action
from unfold.enums import ActionVariant

from admin.mixins import IsSuperuserOnlyModelAdmin
from shipping_acs.enum.shipment_state import AcsShipmentState
from shipping_acs.models import (
    AcsCodPayout,
    AcsPickupList,
    AcsShipment,
    AcsStation,
    AcsTrackingEvent,
)

logger = logging.getLogger(__name__)


# ── Inlines ────────────────────────────────────────────────────────────────


class AcsTrackingEventInline(TabularInline):
    """Read-only event history on the AcsShipment change page."""

    model = AcsTrackingEvent
    extra = 0
    can_delete = False
    fields = (
        "event_time",
        "checkpoint_action",
        "checkpoint_location",
        "notes",
        "received_at",
    )
    readonly_fields = fields
    ordering = ("-event_time",)
    tab = True

    def has_add_permission(self, request, obj=None):
        return False


class AcsShipmentOrderInline(StackedInline):
    """Inline showing the ACS shipment on the Order change page."""

    model = AcsShipment
    extra = 0
    can_delete = False
    max_num = 1
    tab = True
    verbose_name = _("ACS Shipment")
    verbose_name_plural = _("ACS Shipment")

    fieldsets = (
        (
            _("Voucher"),
            {
                "fields": (
                    "voucher_no",
                    "shipment_state",
                    "delivery_date",
                    "last_event_at",
                    "last_polled_at",
                ),
            },
        ),
        (
            _("Destination"),
            {
                "fields": (
                    "delivery_kind",
                    "station_destination",
                    "station_destination_external_id",
                    "station_branch_destination",
                ),
            },
        ),
        (
            _("Parcel"),
            {
                "fields": (
                    "weight_grams",
                    "item_quantity",
                    "charge_type",
                    "cod_amount",
                    "cod_payment_way",
                    "delivery_products",
                ),
                "classes": ("collapse",),
            },
        ),
    )
    readonly_fields = (
        "voucher_no",
        "shipment_state",
        "delivery_date",
        "last_event_at",
        "last_polled_at",
    )

    def has_add_permission(self, request, obj=None):
        # Created at order-creation time by OrderService — never spawn
        # one manually from the Order page.
        return False


# ── Filters ────────────────────────────────────────────────────────────────


class AcsShipmentStateFilter(DropdownFilter):
    title = _("Shipment state")
    parameter_name = "shipment_state"

    def lookups(self, request, model_admin):
        return AcsShipmentState.choices

    def queryset(self, request, queryset):
        value = self.value()
        if value:
            return queryset.filter(shipment_state=value)
        return queryset


# ── Admins ─────────────────────────────────────────────────────────────────


@admin.register(AcsShipment)
class AcsShipmentAdmin(ModelAdmin):
    list_display = (
        "voucher_no",
        "order_link",
        "shipment_state",
        "delivery_kind",
        "pickup_list",
        "last_polled_at",
    )
    list_filter = (
        AcsShipmentStateFilter,
        "delivery_kind",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("voucher_no", "order__id", "order__email")
    readonly_fields = (
        "voucher_no",
        "shipment_state",
        "last_polled_at",
        "last_event_at",
        "stale_alert_sent",
        "delivery_date",
        "delivery_flag",
        "returned_flag",
        "raw_shipment_status",
        "metadata",
        "created_at",
        "updated_at",
    )
    inlines = [AcsTrackingEventInline]
    actions_row = ["repoll_tracking", "issue_voucher_now"]
    actions = ["bulk_repoll_tracking", "retire_shipments"]

    @action(
        description=str(_("Re-poll tracking for selected shipments")),
        icon="refresh",
        variant=ActionVariant.INFO,
    )
    def bulk_repoll_tracking(self, request, queryset):
        """Fan out a poll task per selected shipment.

        Useful when several shipments are stuck on a stale state —
        ACS doesn't push, we poll. We dispatch one Celery task per
        shipment instead of making the admin block on N HTTP calls.
        """

        from shipping_acs.tasks import poll_acs_tracking_one

        count = 0
        for shipment_id in queryset.values_list("id", flat=True):
            poll_acs_tracking_one.delay(int(shipment_id))
            count += 1
        self.message_user(
            request,
            _("Dispatched tracking poll for %(count)d shipment(s).")
            % {"count": count},
            messages.INFO,
        )

    @action(
        description=str(
            _("Retire selected shipments (mark CANCELED, stop polling)")
        ),
        icon="block",
        variant=ActionVariant.DANGER,
    )
    def retire_shipments(self, request, queryset):
        """Locally mark dead shipments CANCELED so the poller skips them.

        For vouchers ACS will never advance again (never handed over,
        or stuck with no resolution) — the target of the stale-shipment
        alert email. Local bookkeeping only: it does NOT call
        ``ACS_Delete_Voucher`` and does NOT touch the order. Already-
        terminal rows are skipped. Per-object ``save()`` (not
        ``.update()``) so simple-history records who retired what.
        """
        retired = 0
        for shipment in queryset.exclude(
            shipment_state__in=[
                AcsShipmentState.DELIVERED,
                AcsShipmentState.RETURNED,
                AcsShipmentState.CANCELED,
                AcsShipmentState.LOST,
            ]
        ):
            shipment.shipment_state = AcsShipmentState.CANCELED
            shipment._change_reason = "Retired via admin action"
            shipment.save(update_fields=["shipment_state"])
            retired += 1
        self.message_user(
            request,
            _("Retired %(count)d shipment(s) — polling stops for them.")
            % {"count": retired},
            messages.WARNING,
        )

    @admin.display(description=_("Order"))
    def order_link(self, obj: AcsShipment) -> str:
        return f"#{obj.order_id}"

    @action(
        description=str(_("Re-poll ACS tracking")),
        variant=ActionVariant.INFO,
    )
    def repoll_tracking(self, request, object_id):
        from shipping_acs.tasks import poll_acs_tracking_one

        poll_acs_tracking_one.delay(int(object_id))
        messages.info(request, _("Tracking poll dispatched."))

    @action(
        description=str(_("Issue ACS voucher now")),
        variant=ActionVariant.PRIMARY,
    )
    def issue_voucher_now(self, request, object_id):
        from shipping_acs.models import AcsShipment as _Shipment
        from shipping_acs.tasks import create_acs_voucher_for_order

        try:
            shipment = _Shipment.objects.get(pk=object_id)
        except _Shipment.DoesNotExist:
            messages.error(request, _("Shipment not found."))
            return
        create_acs_voucher_for_order.delay(shipment.order_id)
        messages.info(request, _("Voucher creation task dispatched."))


@admin.register(AcsStation)
class AcsStationAdmin(ModelAdmin):
    list_display = (
        "external_id",
        "name",
        "shop_kind",
        "city",
        "postal_code",
        "country_code",
        "is_active",
    )
    list_filter = ("shop_kind", "country_code", "is_active")
    search_fields = ("external_id", "name", "city", "postal_code")
    readonly_fields = ("last_synced_at", "created_at", "updated_at")


@admin.register(AcsPickupList)
class AcsPickupListAdmin(IsSuperuserOnlyModelAdmin, ModelAdmin):
    list_display = (
        "pickup_list_no",
        "issued_at",
        "voucher_count",
        "issued_by",
    )
    search_fields = ("pickup_list_no",)
    readonly_fields = (
        "pickup_list_no",
        "issued_at",
        "issued_by",
        "billing_code",
        "voucher_count",
        "metadata",
        "created_at",
        "updated_at",
    )


@admin.register(AcsCodPayout)
class AcsCodPayoutAdmin(IsSuperuserOnlyModelAdmin, ModelAdmin):
    """Read-only ledger of ACS COD payouts (Phase 4c).

    Populated by the daily ``reconcile_acs_cod_payouts`` Celery task.
    All fields are read-only — admins reconcile by linking to the
    underlying shipment / order, not by editing payout rows.
    """

    list_display = (
        "voucher_no",
        "parcel_delivery_date",
        "cod_amount_total",
        "cod_payment_date",
        "shipment",
        "customer_ref_no_1",
    )
    list_filter = (
        ("cod_payment_date", RangeDateFilter),
        ("parcel_delivery_date", RangeDateTimeFilter),
    )
    search_fields = (
        "voucher_no",
        "customer_ref_no_1",
        "customer_ref_no_2",
        "parcel_receiver",
    )
    readonly_fields = (
        "voucher_no",
        "shipment",
        "customer_code",
        "pod",
        "parcel_sender",
        "parcel_receiver",
        "parcel_pickup_date",
        "parcel_delivery_date",
        "customer_ref_no_1",
        "customer_ref_no_2",
        "cod_amount_total",
        "cod_amount_cash",
        "cod_amount_card",
        "cod_payment_date",
        "raw_payload",
        "created_at",
        "updated_at",
    )
    actions_row = ["run_reconciliation"]

    @action(
        description=str(_("Run COD reconciliation now")),
        variant=ActionVariant.PRIMARY,
    )
    def run_reconciliation(self, request, object_id):
        from shipping_acs.tasks import reconcile_acs_cod_payouts

        reconcile_acs_cod_payouts.delay()
        messages.info(request, _("COD reconciliation task dispatched."))


@admin.register(AcsTrackingEvent)
class AcsTrackingEventAdmin(IsSuperuserOnlyModelAdmin, ModelAdmin):
    """Read-only audit trail of ACS tracking webhook deliveries."""

    compressed_fields = True
    list_fullwidth = True
    list_filter_sheet = True

    list_display = (
        "shipment",
        "event_time",
        "checkpoint_action",
        "checkpoint_location",
        "received_at",
    )
    list_filter = (
        "checkpoint_action",
        ("received_at", RangeDateTimeFilter),
    )
    search_fields = (
        "shipment__voucher_no",
        "checkpoint_action",
        "checkpoint_location",
    )
    readonly_fields = (
        "shipment",
        "event_time",
        "checkpoint_action",
        "checkpoint_location",
        "notes",
        "raw_payload",
        "received_at",
        "created_at",
        "updated_at",
    )
    ordering = ("-received_at",)

    def has_add_permission(self, request):
        return False
