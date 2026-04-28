import logging

from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import conditional_escape, format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
)
from unfold.decorators import action
from unfold.enums import ActionVariant

from shipping_boxnow.enum.parcel_state import BoxNowParcelState
from shipping_boxnow.models import (
    BoxNowLocker,
    BoxNowParcelEvent,
    BoxNowShipment,
)

logger = logging.getLogger(__name__)


# ── Inline ─────────────────────────────────────────────────────────────────


class BoxNowParcelEventInline(TabularInline):
    model = BoxNowParcelEvent
    extra = 0
    can_delete = False
    fields = (
        "event_time",
        "event_type",
        "parcel_state",
        "display_name",
        "postal_code",
        "additional_information",
        "received_at",
    )
    readonly_fields = fields
    ordering = ("-event_time",)
    tab = True

    def has_add_permission(self, request, obj=None):
        return False


class BoxNowShipmentOrderInline(StackedInline):
    """Inline for the OneToOne ``BoxNowShipment`` on the Order change page.

    Why this is here and not on ``BoxNowShipmentAdmin``:
        Admins handling support tickets land on the Order page first.
        Showing the BoxNow voucher / locker / parcel state inline saves
        them an extra navigation hop and makes the stage-testing
        workflow (manually setting ``locker_external_id`` to ``4`` per
        BoxNow's email) one click away.

    Locker is editable (stage workaround); ``parcel_id``,
    ``delivery_request_id``, and ``parcel_state`` are managed by the
    Celery task / webhook handler and stay read-only.
    """

    model = BoxNowShipment
    extra = 0
    can_delete = False
    max_num = 1
    tab = True
    verbose_name = _("BoxNow Shipment")
    verbose_name_plural = _("BoxNow Shipment")

    fieldsets = (
        (
            _("BoxNow Identifiers"),
            {
                "fields": (
                    "parcel_id",
                    "delivery_request_id",
                    "parcel_state",
                    "last_event_at",
                ),
            },
        ),
        (
            _("Locker"),
            {
                "fields": (
                    "locker_external_id",
                    "locker",
                    "compartment_size",
                    "weight_grams",
                ),
                "description": _(
                    "Set ``Locker External ID`` to ``4`` for stage "
                    "testing per BoxNow's onboarding email."
                ),
            },
        ),
        (
            _("Payment & Returns"),
            {
                "fields": (
                    "payment_mode",
                    "amount_to_be_collected",
                    "allow_return",
                ),
                "classes": ("collapse",),
            },
        ),
    )
    readonly_fields = (
        "parcel_id",
        "delivery_request_id",
        "parcel_state",
        "last_event_at",
    )

    def has_add_permission(self, request, obj=None):
        # The shipment row is created at order-creation time by
        # OrderService — admins should never spawn one manually.
        return False


# ── Filters ────────────────────────────────────────────────────────────────


class BoxNowParcelStateBadgeFilter(DropdownFilter):
    """
    Groups parcel states into broad operational categories so the admin
    can quickly filter without knowing every individual state value.
    """

    title = _("Parcel State Group")
    parameter_name = "parcel_state_group"

    def lookups(self, request, model_admin):
        return [
            ("active", _("Active (Pending / In transit)")),
            ("ready_for_pickup", _("Ready for Pickup")),
            ("completed", _("Completed")),
            (
                "problematic",
                _("Problematic (Returned / Expired / Canceled / Lost)"),
            ),
        ]

    def queryset(self, request, queryset):
        filter_value = self.value()

        match filter_value:
            case "active":
                filter_kwargs = {
                    "parcel_state__in": [
                        BoxNowParcelState.PENDING_CREATION,
                        BoxNowParcelState.NEW,
                        BoxNowParcelState.IN_DEPOT,
                        BoxNowParcelState.ACCEPTED_TO_LOCKER,
                        BoxNowParcelState.ACCEPTED_FOR_RETURN,
                    ]
                }
            case "ready_for_pickup":
                filter_kwargs = {
                    "parcel_state": BoxNowParcelState.FINAL_DESTINATION
                }
            case "completed":
                filter_kwargs = {"parcel_state": BoxNowParcelState.DELIVERED}
            case "problematic":
                filter_kwargs = {
                    "parcel_state__in": [
                        BoxNowParcelState.RETURNED,
                        BoxNowParcelState.EXPIRED,
                        BoxNowParcelState.CANCELED,
                        BoxNowParcelState.LOST,
                        BoxNowParcelState.MISSING,
                    ]
                }
            case _:
                return queryset

        return queryset.filter(**filter_kwargs)


# ── Helpers ────────────────────────────────────────────────────────────────

_STATE_BADGE_CONFIG: dict[str, dict[str, str]] = {
    BoxNowParcelState.PENDING_CREATION: {
        "bg": "bg-gray-50 dark:bg-gray-900",
        "text": "text-gray-600 dark:text-gray-300",
        "icon": "⏳",
    },
    BoxNowParcelState.NEW: {
        "bg": "bg-blue-50 dark:bg-blue-900",
        "text": "text-blue-700 dark:text-blue-300",
        "icon": "🆕",
    },
    BoxNowParcelState.IN_DEPOT: {
        "bg": "bg-cyan-50 dark:bg-cyan-900",
        "text": "text-cyan-700 dark:text-cyan-300",
        "icon": "📦",
    },
    BoxNowParcelState.ACCEPTED_TO_LOCKER: {
        "bg": "bg-cyan-50 dark:bg-cyan-900",
        "text": "text-cyan-700 dark:text-cyan-300",
        "icon": "🔒",
    },
    BoxNowParcelState.ACCEPTED_FOR_RETURN: {
        "bg": "bg-yellow-50 dark:bg-yellow-900",
        "text": "text-yellow-700 dark:text-yellow-300",
        "icon": "↩️",
    },
    BoxNowParcelState.FINAL_DESTINATION: {
        "bg": "bg-amber-50 dark:bg-amber-900",
        "text": "text-amber-700 dark:text-amber-300",
        "icon": "🏁",
    },
    BoxNowParcelState.DELIVERED: {
        "bg": "bg-green-50 dark:bg-green-900",
        "text": "text-green-700 dark:text-green-300",
        "icon": "✅",
    },
    BoxNowParcelState.RETURNED: {
        "bg": "bg-red-50 dark:bg-red-900",
        "text": "text-red-700 dark:text-red-300",
        "icon": "↩️",
    },
    BoxNowParcelState.EXPIRED: {
        "bg": "bg-red-50 dark:bg-red-900",
        "text": "text-red-700 dark:text-red-300",
        "icon": "🕐",
    },
    BoxNowParcelState.CANCELED: {
        "bg": "bg-red-50 dark:bg-red-900",
        "text": "text-red-700 dark:text-red-300",
        "icon": "❌",
    },
    BoxNowParcelState.LOST: {
        "bg": "bg-red-50 dark:bg-red-900",
        "text": "text-red-700 dark:text-red-300",
        "icon": "❓",
    },
    BoxNowParcelState.MISSING: {
        "bg": "bg-red-50 dark:bg-red-900",
        "text": "text-red-700 dark:text-red-300",
        "icon": "🔍",
    },
}

_FALLBACK_BADGE = {
    "bg": "bg-gray-50 dark:bg-gray-900",
    "text": "text-gray-600 dark:text-gray-300",
    "icon": "❓",
}


def _render_state_badge(state: str, display: str) -> str:
    config = _STATE_BADGE_CONFIG.get(state, _FALLBACK_BADGE)
    safe_display = conditional_escape(display)
    html = (
        f'<span class="inline-flex items-center justify-center px-2 py-1 '
        f'text-xs font-medium {config["bg"]} {config["text"]} rounded-full gap-1">'
        f"<span>{config['icon']}</span>"
        f"<span>{safe_display}</span>"
        "</span>"
    )
    return mark_safe(html)


# ── BoxNowShipment admin ────────────────────────────────────────────────────


@admin.register(BoxNowShipment)
class BoxNowShipmentAdmin(ModelAdmin):
    compressed_fields = True
    warn_unsaved_form = True
    list_fullwidth = True
    list_filter_submit = True
    list_filter_sheet = True

    list_display = (
        "parcel_id_display",
        "parcel_state_badge",
        "locker_external_id",
        "order_link",
        "compartment_size",
        "created_at",
    )
    list_filter = (
        BoxNowParcelStateBadgeFilter,
        "parcel_state",
        "payment_mode",
        "compartment_size",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = (
        "parcel_id",
        "delivery_request_id",
        "locker_external_id",
        "order__email",
        "order__id",
    )
    readonly_fields = (
        "uuid",
        "delivery_request_id",
        "parcel_id",
        "label_url",
        "metadata",
        "created_at",
        "updated_at",
        "last_event_at",
        "cancel_requested_at",
    )
    fieldsets = (
        (
            _("BoxNow Identifiers"),
            {
                "fields": (
                    "uuid",
                    "delivery_request_id",
                    "parcel_id",
                    "label_url",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Order & Locker"),
            {
                "fields": (
                    "order",
                    "locker",
                    "locker_external_id",
                ),
                "classes": ("wide",),
                "description": _(
                    "Edit 'Locker External ID' directly to override the "
                    "locker for stage testing (e.g. set to '4' per BoxNow's "
                    "stage instructions)."
                ),
            },
        ),
        (
            _("Shipment Configuration"),
            {
                "fields": (
                    "parcel_state",
                    "compartment_size",
                    "weight_grams",
                    "payment_mode",
                    "amount_to_be_collected",
                    "allow_return",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Timestamps"),
            {
                "fields": (
                    "created_at",
                    "updated_at",
                    "last_event_at",
                    "cancel_requested_at",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Diagnostics"),
            {
                "fields": ("metadata",),
                "classes": ("collapse",),
            },
        ),
    )
    inlines = [BoxNowParcelEventInline]
    actions = ["cancel_parcels", "resync_label"]
    actions_detail = ["cancel_parcel_action"]
    list_select_related = ["order", "locker"]
    save_on_top = True
    date_hierarchy = "created_at"

    # ── List display helpers ────────────────────────────────────────

    @admin.display(description=_("Voucher"))
    def parcel_id_display(self, obj):
        return obj.parcel_id or "—"

    @admin.display(description=_("State"))
    def parcel_state_badge(self, obj):
        display = obj.get_parcel_state_display()
        return _render_state_badge(obj.parcel_state, display)

    @admin.display(description=_("Order"))
    def order_link(self, obj):
        url = reverse("admin:order_order_change", args=[obj.order_id])
        safe_url = conditional_escape(url)
        safe_id = conditional_escape(str(obj.order_id))
        return format_html('<a href="{}">#{}</a>', safe_url, safe_id)

    # ── List (bulk) actions ─────────────────────────────────────────

    @action(
        description=str(_("Cancel parcels via BoxNow API")),
        variant=ActionVariant.DANGER,
        icon="cancel",
    )
    def cancel_parcels(self, request, queryset):
        from shipping_boxnow.services import BoxNowService  # noqa: PLC0415

        cancelable = queryset.filter(parcel_state=BoxNowParcelState.NEW)
        skipped = queryset.exclude(parcel_state=BoxNowParcelState.NEW).count()

        success_count = 0
        error_count = 0

        for shipment in cancelable:
            try:
                BoxNowService.cancel_shipment(
                    shipment, reason="admin bulk cancel"
                )
                success_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Admin cancel_parcels failed for shipment %s",
                    shipment.pk,
                )
                messages.error(
                    request,
                    _("Failed to cancel parcel %(parcel_id)s: %(err)s")
                    % {
                        "parcel_id": shipment.parcel_id or shipment.pk,
                        "err": str(exc),
                    },
                )
                error_count += 1

        if success_count:
            messages.success(
                request,
                _("%(count)d parcel(s) canceled successfully.")
                % {"count": success_count},
            )
        if skipped:
            messages.warning(
                request,
                _(
                    "%(count)d shipment(s) skipped — only parcels in "
                    "'new' state can be canceled via BoxNow API."
                )
                % {"count": skipped},
            )

    @action(
        description=str(_("Re-fetch label URL from BoxNow")),
        variant=ActionVariant.INFO,
        icon="download",
    )
    def resync_label(self, request, queryset):
        from shipping_boxnow.services import BoxNowService  # noqa: PLC0415

        success_count = 0
        error_count = 0

        for shipment in queryset.exclude(parcel_id__isnull=True):
            try:
                BoxNowService.fetch_label_bytes(shipment)
                success_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Admin resync_label failed for shipment %s",
                    shipment.pk,
                )
                messages.error(
                    request,
                    _("Failed to fetch label for parcel %(parcel_id)s: %(err)s")
                    % {
                        "parcel_id": shipment.parcel_id,
                        "err": str(exc),
                    },
                )
                error_count += 1

        no_id_count = queryset.filter(parcel_id__isnull=True).count()

        if success_count:
            messages.success(
                request,
                _("%(count)d label(s) re-fetched successfully.")
                % {"count": success_count},
            )
        if no_id_count:
            messages.warning(
                request,
                _("%(count)d shipment(s) skipped — no parcel ID assigned yet.")
                % {"count": no_id_count},
            )

    # ── Detail actions ──────────────────────────────────────────────

    @action(
        description=str(_("Cancel parcel via BoxNow API")),
        variant=ActionVariant.DANGER,
        icon="cancel",
    )
    def cancel_parcel_action(self, request, object_id):
        """Cancel a single shipment from the detail page."""
        from django.shortcuts import redirect  # noqa: PLC0415

        from shipping_boxnow.services import BoxNowService  # noqa: PLC0415

        try:
            shipment = BoxNowShipment.objects.get(pk=object_id)
        except BoxNowShipment.DoesNotExist:
            messages.error(request, _("Shipment not found."))
            return redirect(
                reverse(
                    "admin:shipping_boxnow_boxnowshipment_change",
                    args=[object_id],
                )
            )

        if shipment.parcel_state != BoxNowParcelState.NEW:
            messages.warning(
                request,
                _(
                    "Parcel %(parcel_id)s is in state '%(state)s' and cannot be "
                    "canceled via BoxNow API. Only 'new' parcels are cancelable."
                )
                % {
                    "parcel_id": shipment.parcel_id or shipment.pk,
                    "state": shipment.get_parcel_state_display(),
                },
            )
            return redirect(
                reverse(
                    "admin:shipping_boxnow_boxnowshipment_change",
                    args=[object_id],
                )
            )

        try:
            BoxNowService.cancel_shipment(shipment, reason="admin cancel")
            messages.success(
                request,
                _("Parcel %(parcel_id)s canceled successfully.")
                % {"parcel_id": shipment.parcel_id},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Admin cancel_parcel_action failed for shipment %s",
                shipment.pk,
            )
            messages.error(
                request,
                _("Cancel failed: %(err)s") % {"err": str(exc)},
            )

        return redirect(
            reverse(
                "admin:shipping_boxnow_boxnowshipment_change",
                args=[object_id],
            )
        )


# ── BoxNowLocker admin ──────────────────────────────────────────────────────


@admin.register(BoxNowLocker)
class BoxNowLockerAdmin(ModelAdmin):
    compressed_fields = True
    list_fullwidth = True
    list_filter_submit = True

    list_display = (
        "external_id",
        "name",
        "postal_code",
        "address_line_1",
        "is_active",
        "last_synced_at",
    )
    list_filter = (
        "is_active",
        "type",
        ("last_synced_at", RangeDateTimeFilter),
    )
    search_fields = (
        "external_id",
        "name",
        "postal_code",
        "address_line_1",
        "address_line_2",
    )
    readonly_fields = (
        "uuid",
        "last_synced_at",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            _("Identification"),
            {
                "fields": (
                    "uuid",
                    "external_id",
                    "type",
                    "is_active",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Location"),
            {
                "fields": (
                    "name",
                    "title",
                    "address_line_1",
                    "address_line_2",
                    "postal_code",
                    "country_code",
                    "lat",
                    "lng",
                    "note",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Media & Sync"),
            {
                "fields": (
                    "image_url",
                    "last_synced_at",
                    "created_at",
                    "updated_at",
                ),
                "classes": ("wide",),
            },
        ),
    )
    actions = ["sync_from_boxnow"]
    save_on_top = True

    @action(
        description=str(_("Sync lockers from BoxNow API")),
        variant=ActionVariant.INFO,
        icon="sync",
    )
    def sync_from_boxnow(self, request, queryset):
        """
        Trigger a full locker sync from the BoxNow destination API.

        The queryset is intentionally ignored — ``sync_lockers()`` always
        fetches and upserts the full set of active APM locations.
        """
        from shipping_boxnow.services import BoxNowService  # noqa: PLC0415

        try:
            result = BoxNowService.sync_lockers()
            messages.success(
                request,
                _(
                    "BoxNow locker sync complete: %(created)d created, "
                    "%(updated)d updated, %(deactivated)d deactivated."
                )
                % {
                    "created": result.get("created", 0),
                    "updated": result.get("updated", 0),
                    "deactivated": result.get("deactivated", 0),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("BoxNow locker sync failed")
            messages.error(
                request,
                _("Locker sync failed: %(err)s") % {"err": str(exc)},
            )


# ── BoxNowParcelEvent admin ─────────────────────────────────────────────────


@admin.register(BoxNowParcelEvent)
class BoxNowParcelEventAdmin(ModelAdmin):
    compressed_fields = True
    list_fullwidth = True
    list_filter_submit = True

    list_display = (
        "shipment_voucher",
        "event_type",
        "parcel_state_badge",
        "event_time",
        "display_name",
        "received_at",
    )
    list_filter = (
        "event_type",
        "parcel_state",
        ("event_time", RangeDateTimeFilter),
    )
    search_fields = (
        "shipment__parcel_id",
        "webhook_message_id",
    )
    readonly_fields = (
        "shipment",
        "webhook_message_id",
        "event_type",
        "parcel_state",
        "event_time",
        "display_name",
        "postal_code",
        "additional_information",
        "raw_payload",
        "received_at",
        "created_at",
        "updated_at",
    )

    # All events are immutable audit records — no fieldset editing.
    fieldsets = (
        (
            _("Event"),
            {
                "fields": (
                    "shipment",
                    "webhook_message_id",
                    "event_type",
                    "parcel_state",
                    "event_time",
                    "received_at",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Location"),
            {
                "fields": (
                    "display_name",
                    "postal_code",
                    "additional_information",
                ),
                "classes": ("wide",),
            },
        ),
        (
            _("Raw Payload"),
            {
                "fields": ("raw_payload",),
                "classes": ("collapse",),
            },
        ),
    )
    list_select_related = ["shipment"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_("Voucher"), ordering="shipment__parcel_id")
    def shipment_voucher(self, obj):
        parcel_id = obj.shipment.parcel_id if obj.shipment_id else "—"
        return parcel_id or "—"

    @admin.display(description=_("Parcel State"))
    def parcel_state_badge(self, obj):
        if not obj.parcel_state:
            return "—"
        # parcel_state on the event is the raw BoxNow vocabulary string,
        # not necessarily a BoxNowParcelState enum value.  We try to map
        # it for the colour config; fall back gracefully.
        display = obj.parcel_state
        return _render_state_badge(obj.parcel_state, display)
