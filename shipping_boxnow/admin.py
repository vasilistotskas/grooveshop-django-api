import logging

from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import conditional_escape, format_html
from django.utils.translation import gettext_lazy as _
from unfold.admin import StackedInline, TabularInline
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateTimeFilter,
)
from unfold.decorators import action, display
from unfold.enums import ActionVariant

from admin.base import BaseModelAdmin
from admin.displays import SHIPMENT_STATE_VARIANT, choice_label
from admin.mixins import IsSuperuserOnlyModelAdmin
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
    per_page = 15
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


# ── BoxNowShipment admin ────────────────────────────────────────────────────


@admin.register(BoxNowShipment)
class BoxNowShipmentAdmin(BaseModelAdmin):
    list_display = (
        "parcel_id_display",
        "parcel_state_label",
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
        # parcel_state is carrier-managed via the webhook state machine
        # (which also syncs Order status + writes history). Editing it
        # directly in admin would desync those, so it is read-only here —
        # matching the inline (G0057).
        "parcel_state",
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
    actions = ["cancel_parcels", "resync_label", "download_labels_zip"]
    actions_detail = ["download_voucher_action", "cancel_parcel_action"]
    list_select_related = ["order", "locker"]
    date_hierarchy = "created_at"

    parcel_state_label = choice_label(
        "parcel_state",
        variants=SHIPMENT_STATE_VARIANT,
        description=_("State"),
    )

    # ── List display helpers ────────────────────────────────────────

    @admin.display(description=_("Voucher"))
    def parcel_id_display(self, obj):
        return obj.parcel_id or "—"

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

    @action(
        description=str(_("Download voucher PDFs (zip)")),
        variant=ActionVariant.PRIMARY,
        icon="folder_zip",
    )
    def download_labels_zip(self, request, queryset):
        """Stream a single zip containing voucher PDFs for the queryset.

        Saves the warehouse team N round-trips when batching pickups —
        select the day's shipments, click the action, get one zip with
        all the labels named ``boxnow-voucher-<parcel_id>.pdf``.
        Shipments without a ``parcel_id`` are silently skipped (the
        BoxNow API has no voucher to issue yet).
        """

        import io  # noqa: PLC0415
        import zipfile  # noqa: PLC0415

        from django.http import HttpResponse  # noqa: PLC0415

        from shipping_boxnow.services import BoxNowService  # noqa: PLC0415

        eligible = queryset.exclude(parcel_id__isnull=True).exclude(
            parcel_id__exact=""
        )
        if not eligible.exists():
            messages.warning(
                request,
                _(
                    "Selection contains no shipments with a parcel_id — "
                    "nothing to bundle."
                ),
            )
            return None

        buf = io.BytesIO()
        ok = err = 0
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for shipment in eligible:
                try:
                    pdf = BoxNowService.fetch_label_bytes(shipment)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "download_labels_zip failed for shipment %s",
                        shipment.pk,
                    )
                    err += 1
                    continue
                zf.writestr(f"boxnow-voucher-{shipment.parcel_id}.pdf", pdf)
                ok += 1

        if not ok:
            messages.error(
                request,
                _("All %(err)d shipment(s) failed — see server logs.")
                % {"err": err},
            )
            return None
        if err:
            messages.warning(
                request,
                _(
                    "%(err)d shipment(s) failed and were skipped — "
                    "see server logs."
                )
                % {"err": err},
            )

        buf.seek(0)
        response = HttpResponse(buf.read(), content_type="application/zip")
        response["Content-Disposition"] = (
            'attachment; filename="boxnow-vouchers.zip"'
        )
        return response

    # ── Detail actions ──────────────────────────────────────────────

    @action(
        description=str(_("Download BoxNow voucher (PDF)")),
        variant=ActionVariant.PRIMARY,
        icon="download",
    )
    def download_voucher_action(self, request, object_id):
        """Stream the BoxNow voucher PDF for a single shipment.

        Calls ``BoxNowService.fetch_label_bytes`` (which hits BoxNow's
        ``/api/v1/parcels/{id}/label.pdf`` and caches for an hour) and
        returns the bytes inline so the operator can preview-then-print
        from any modern browser. The ``attachment`` Content-Disposition
        keeps the filename predictable (``boxnow-voucher-<parcel>.pdf``)
        for filing.

        Falls back to a redirect with a flash message when the shipment
        has no parcel_id yet (e.g. ``parcel_state=pending_creation``)
        rather than 404'ing — admins are usually triaging stuck rows.
        """
        from django.http import HttpResponse  # noqa: PLC0415
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

        if not shipment.parcel_id:
            messages.warning(
                request,
                _(
                    "No voucher available — shipment is in state "
                    "'%(state)s'. The BoxNow delivery-request task has "
                    "not yet assigned a parcel ID for this order."
                )
                % {"state": shipment.get_parcel_state_display()},
            )
            return redirect(
                reverse(
                    "admin:shipping_boxnow_boxnowshipment_change",
                    args=[object_id],
                )
            )

        try:
            pdf_bytes = BoxNowService.fetch_label_bytes(shipment)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Admin download_voucher_action failed for shipment %s",
                shipment.pk,
            )
            messages.error(
                request,
                _("Failed to fetch voucher for parcel %(parcel_id)s: %(err)s")
                % {"parcel_id": shipment.parcel_id, "err": str(exc)},
            )
            return redirect(
                reverse(
                    "admin:shipping_boxnow_boxnowshipment_change",
                    args=[object_id],
                )
            )

        filename = f"boxnow-voucher-{shipment.parcel_id}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Content-Length"] = str(len(pdf_bytes))
        return response

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
class BoxNowLockerAdmin(BaseModelAdmin):
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
class BoxNowParcelEventAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    list_display = (
        "shipment_voucher",
        "event_type",
        "parcel_state_label",
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

    # ``parcel_state`` on the event is the raw BoxNow vocabulary string
    # (see ``BoxNowParcelEvent`` docstring) — the field has no
    # ``choices=``, so ``get_parcel_state_display()`` doesn't exist and
    # ``admin.displays.choice_label`` can't be used here. This mirrors
    # its behaviour directly: render the raw value through the shared
    # variant map, falling back to unfold's default "-" for empty rows.
    @display(description=_("Parcel State"), label=SHIPMENT_STATE_VARIANT)
    def parcel_state_label(self, obj):
        return obj.parcel_state or None
