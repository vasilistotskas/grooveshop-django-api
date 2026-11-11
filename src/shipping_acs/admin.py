"""Django admin for ACS shipping models — uses unfold for theming.

Mirrors :mod:`shipping_boxnow.admin` so support flows are consistent
across providers.
"""

from __future__ import annotations

import logging

from django import forms
from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.html import format_html, format_html_join
from django.utils.translation import gettext_lazy as _
from unfold.admin import StackedInline, TabularInline
from unfold.contrib.filters.admin import (
    DropdownFilter,
    RangeDateFilter,
    RangeDateTimeFilter,
)
from unfold.dataclasses import ActionDialog
from unfold.decorators import action
from unfold.enums import ActionVariant
from unfold.forms import BaseDialogForm
from unfold.widgets import (
    UnfoldAdminTextareaWidget,
    UnfoldAdminTextInputWidget,
)

from admin.base import BaseModelAdmin
from admin.displays import SHIPMENT_STATE_VARIANT, change_link, choice_label
from admin.mixins import IsSuperuserOnlyModelAdmin
from order.models.order import Order
from shipping_acs import voucher_hold
from shipping_acs.attention import (
    needs_no_voucher,
    stale_shipment_q,
    unattributed_payout_q,
    unremitted_cod_shipments,
    unresolved_unrecognised_voucher_q,
    voucher_hold_in_force_q,
    voucher_request_held_q,
    voucher_request_unknown,
    voucher_request_unknown_q,
    voucher_requests_held,
)
from shipping_acs.diagnostics import shipment_timeline
from shipping_acs.enum.shipment_state import TERMINAL_STATES, AcsShipmentState
from shipping_acs.exceptions import AcsVoucherRequestUnknownError
from shipping_acs.models import (
    AcsApiCall,
    AcsCodPayout,
    AcsPickupList,
    AcsPickupListRun,
    AcsShipment,
    AcsStation,
    AcsTrackingEvent,
    AcsUnrecognisedVoucher,
    AcsVoucherHold,
)
from shipping_acs.tasks import issue_held_acs_vouchers
from tenant.celery import dispatch_on_commit

logger = logging.getLogger(__name__)


def voucher_check_message(shipment: AcsShipment) -> str:
    """What staff do with a shipment whose voucher request has an unknown
    outcome, by the shared rule (:func:`shipping_acs.attention.needs_no_voucher`): close it, or adopt / request again."""
    if needs_no_voucher(shipment):
        message = _(
            "The last voucher request for order #%(order)s may have reached "
            "ACS without an answer, and the order no longer needs a voucher. "
            "If ACS holds one, delete it there, then use “No voucher needed: "
            "close” on the shipment."
        )
    else:
        message = _(
            "The last voucher request for order #%(order)s may have reached "
            "ACS without an answer, so ACS may already hold a voucher for it. "
            "Check the ACS portal, then use “Adopt ACS voucher” or “No "
            "voucher at ACS: request again” on the shipment."
        )
    return str(message % {"order": shipment.order_id})


def _back_to_changelist(model_admin):
    """Return the response every URL-backed unfold action owes Django.

    ``actions_row``, ``actions_list`` and ``actions_detail`` are
    registered as real URLs (``unfold`` ModelAdmin.get_urls), and
    ``@action`` hands the decorated method's return value straight to
    Django. An action that falls off the end therefore returns ``None``
    and Django raises "didn't return an HttpResponse" — the button does
    its work and *then* 500s, so the operator sees a server error and no
    confirmation of an action that actually ran.

    ``actions_submit_line`` is exempt: it is invoked from
    ``save_model``, not routed as a URL.
    """
    opts = model_admin.model._meta
    return redirect(
        reverse(f"admin:{opts.app_label}_{opts.model_name}_changelist")
    )


def _local_time(moment) -> str:
    """``moment`` in the store's time zone, as the admin writes dates."""
    return date_format(timezone.localtime(moment), "SHORT_DATETIME_FORMAT")


class CodReceivedOutsideAcsForm(BaseDialogForm):
    note = forms.CharField(
        label=_("Note"),
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
        help_text=_(
            "How the money arrived (bank transfer reference, hand "
            "delivery, ...). Recorded on the shipment's audit trail."
        ),
    )


class CancelVoucherForm(BaseDialogForm):
    reason = forms.CharField(
        label=_("Reason"),
        required=False,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
        help_text=_(
            "Why the voucher is cancelled (for example a corrected "
            "address). Kept on the shipment."
        ),
    )


class AdoptVoucherForm(BaseDialogForm):
    voucher_no = forms.CharField(
        label=_("Voucher number"),
        max_length=20,
        widget=UnfoldAdminTextInputWidget(),
        help_text=_("The voucher number as the ACS portal shows it."),
    )
    confirmed_by_hand = forms.CharField(
        label=_("Confirmed by hand"),
        required=False,
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
        help_text=_(
            "Only when ACS spells the recipient differently from the voucher "
            "request: how you confirmed in the ACS portal that this is the "
            "order's parcel. Kept on the order."
        ),
    )


class RequestVoucherAgainForm(BaseDialogForm):
    note = forms.CharField(
        label=_("How you checked"),
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
        help_text=_(
            "Where you looked for a voucher of this order at ACS. Kept on "
            "the order."
        ),
    )


class ResolveUnrecognisedVoucherForm(BaseDialogForm):
    note = forms.CharField(
        label=_("Note"),
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
        help_text=_(
            "What the voucher is (made in the ACS portal, handled at an "
            "ACS store, ...). Kept on the voucher."
        ),
    )


class ReconcilePayoutForm(BaseDialogForm):
    note = forms.CharField(
        label=_("Note"),
        widget=UnfoldAdminTextareaWidget(attrs={"rows": 3}),
        help_text=_(
            "How the payout was accounted for (the off-system sale, the "
            "invoice, the line of the ACS report). Kept on the payout."
        ),
    )


# ── Inlines ────────────────────────────────────────────────────────────────


class AcsTrackingEventInline(TabularInline):
    """Read-only event history on the AcsShipment change page."""

    model = AcsTrackingEvent
    extra = 0
    can_delete = False
    per_page = 15
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
                    "diagnostics_link",
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
        "diagnostics_link",
    )

    @admin.display(description=_("Diagnostics"))
    def diagnostics_link(self, obj: AcsShipment) -> str:
        """The shipment's own page, whose Diagnostics tab holds the
        timeline (label, pickup-list runs, tracking, ACS calls)."""
        if not obj.pk:
            return "-"
        return change_link(
            self.admin_site,
            AcsShipment,
            obj.pk,
            _("Open the shipment timeline"),
        )

    def has_add_permission(self, request, obj=None):
        # Created at order-creation time by OrderService — never spawn
        # one manually from the Order page.
        return False


# ── Filters ────────────────────────────────────────────────────────────────


class LabelPrintedFilter(DropdownFilter):
    """ "Not printed" is the pre-flight view for the 16:30 manifest run:
    ACS rejects a pickup list that contains any unprinted voucher, so
    this is where you find them first."""

    title = _("Label")
    parameter_name = "label_printed"

    def lookups(self, request, model_admin):
        return [("yes", _("Printed")), ("no", _("Not printed"))]

    def queryset(self, request, queryset):
        match self.value():
            case "yes":
                return queryset.filter(label_printed_at__isnull=False)
            case "no":
                return queryset.filter(label_printed_at__isnull=True)
            case _:
                return queryset


class NeedsAttentionFilter(DropdownFilter):
    """The parcels the alert emails and the dashboard's "Needs attention"
    counters are about, by the same rules
    (:mod:`shipping_acs.attention`)."""

    title = _("Needs attention")
    parameter_name = "attention"

    def lookups(self, request, model_admin):
        return [
            ("stale", _("Stale: no tracking movement or never created")),
            (
                "voucher_unknown",
                _("Voucher request with an unknown outcome"),
            ),
            (
                "voucher_held",
                _("Voucher request held while ACS is failing"),
            ),
            ("unremitted_cod", _("Delivered COD never paid out by ACS")),
        ]

    def queryset(self, request, queryset):
        match self.value():
            case "stale":
                return queryset.filter(stale_shipment_q())
            case "voucher_unknown":
                return queryset.filter(voucher_request_unknown_q())
            case "voucher_held":
                return queryset.filter(voucher_request_held_q())
            case "unremitted_cod":
                return queryset.filter(
                    pk__in=unremitted_cod_shipments().values("pk")
                )
            case _:
                return queryset


class UnattributedPayoutFilter(DropdownFilter):
    """Payouts that tie to none of the store's shipments and that nobody
    has reconciled: exactly what the dashboard counter counts."""

    title = _("Shipment")
    parameter_name = "unattributed"

    def lookups(self, request, model_admin):
        return [("yes", _("Matches no shipment, not reconciled"))]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(unattributed_payout_q())
        return queryset


class ReconciledPayoutFilter(DropdownFilter):
    """Whether staff have reconciled the payout."""

    title = _("Reconciliation")
    parameter_name = "reconciled"

    def lookups(self, request, model_admin):
        return [("yes", _("Reconciled")), ("no", _("Not reconciled"))]

    def queryset(self, request, queryset):
        match self.value():
            case "yes":
                return queryset.filter(reconciled_at__isnull=False)
            case "no":
                return queryset.filter(reconciled_at__isnull=True)
            case _:
                return queryset


# ── Admins ─────────────────────────────────────────────────────────────────


@admin.register(AcsShipment)
class AcsShipmentAdmin(BaseModelAdmin):
    list_display = (
        "voucher_no",
        "order_link",
        "shipment_state_label",
        "delivery_kind",
        "pickup_list",
        "label_printed_at",
        "last_polled_at",
    )
    date_hierarchy = "created_at"
    list_filter = (
        "shipment_state",
        "delivery_kind",
        LabelPrintedFilter,
        NeedsAttentionFilter,
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("voucher_no", "order__id", "order__email")
    autocomplete_fields = ("order",)
    readonly_fields = (
        "voucher_no",
        "voucher_request_unknown_at",
        "voucher_request_held_at",
        "shipment_state",
        "label_printed_at",
        "last_polled_at",
        "last_event_at",
        "stale_alert_sent",
        "delivery_date",
        "delivery_flag",
        "returned_flag",
        "raw_shipment_status",
        "cod_received_outside_acs_at",
        "cod_received_outside_acs_by",
        "cod_received_outside_acs_note",
        "metadata",
        "diagnostics_timeline",
        "created_at",
        "updated_at",
    )
    fieldsets = (
        (
            _("Shipment"),
            {
                "classes": ("tab",),
                "fields": (
                    "order",
                    "voucher_no",
                    "voucher_request_unknown_at",
                    "voucher_request_held_at",
                    "shipment_state",
                    "delivery_kind",
                    "pickup_list",
                    "label_printed_at",
                    "cancel_requested_at",
                    "arrival_notified_at",
                ),
            },
        ),
        (
            _("Destination"),
            {
                "classes": ("tab",),
                "fields": (
                    "station_destination",
                    "station_destination_external_id",
                    "station_branch_destination",
                ),
            },
        ),
        (
            _("Parcel & COD"),
            {
                "classes": ("tab",),
                "fields": (
                    "weight_grams",
                    "item_quantity",
                    "charge_type",
                    "cod_amount",
                    "cod_payment_way",
                    "delivery_products",
                    "cod_received_outside_acs_at",
                    "cod_received_outside_acs_by",
                    "cod_received_outside_acs_note",
                ),
            },
        ),
        (
            _("Tracking"),
            {
                "classes": ("tab",),
                "fields": (
                    "last_polled_at",
                    "last_event_at",
                    "stale_alert_sent",
                    "delivery_date",
                    "delivery_flag",
                    "returned_flag",
                    "raw_shipment_status",
                ),
            },
        ),
        (
            _("Diagnostics"),
            {
                "classes": ("tab",),
                "fields": (
                    "diagnostics_timeline",
                    "metadata",
                    "created_at",
                    "updated_at",
                ),
            },
        ),
    )
    inlines = [AcsTrackingEventInline]
    # Changelist-level, and deliberately on the SHIPMENT admin rather
    # than AcsPickupListAdmin: that one is IsSuperuserOnlyModelAdmin, so
    # the merchant who prints the labels cannot open it. This is the
    # page they already work on, next to the "not printed" filter.
    actions_list = ["issue_pickup_list_now"]
    actions_row = ["repoll_tracking", "issue_voucher_now"]
    actions_detail = [
        "adopt_voucher",
        "request_voucher_again",
        "close_voucher_request",
        "record_cod_received_outside_acs",
        "cancel_voucher_only",
    ]
    actions = [
        "bulk_print_labels",
        "bulk_repoll_tracking",
        "retire_shipments",
    ]
    list_select_related = ("order", "pickup_list")

    shipment_state_label = choice_label(
        "shipment_state",
        variants=SHIPMENT_STATE_VARIANT,
        description=_("State"),
    )

    @action(
        description=_("Print labels for selected shipments"),
        permissions=["change"],
        icon="print",
        variant=ActionVariant.PRIMARY,
    )
    def bulk_print_labels(self, request, queryset):
        """Download one merged PDF of the selected shipments' labels.

        ACS refuses the whole daily manifest while any voucher on it is
        unprinted, so the operation that unblocks a pickup list is
        "print everything outstanding" — and until this action existed
        that meant opening each order in turn, which is how a voucher
        gets missed. Pair it with the ``Label printed at``/``empty``
        filter on this changelist to select exactly the unprinted ones.

        Downloading stamps ``label_printed_at`` through the same
        ``fetch_label_bytes`` choke point as the per-order button, so
        the pre-flight warning and the manifest see the same truth.
        """
        from shipping_acs.services import AcsService

        shipments = list(queryset.order_by("order_id"))
        pdf_bytes, failures = AcsService.fetch_labels_pdf(shipments)

        for failure in failures:
            self.message_user(
                request,
                _(
                    "Could not print voucher %(voucher)s (order #%(order)s): "
                    "%(error)s"
                )
                % {
                    "voucher": failure["voucher_no"] or "—",
                    "order": failure["order_id"],
                    "error": failure["error"],
                },
                messages.ERROR,
            )

        if not pdf_bytes:
            # Every selected voucher failed; the per-failure messages
            # above already say which and why. Returning None leaves the
            # admin on the changelist with those errors displayed.
            return None

        if failures:
            self.message_user(
                request,
                _(
                    "Printed %(ok)d of %(total)d selected label(s); "
                    "%(failed)d could not be fetched."
                )
                % {
                    "ok": len(shipments) - len(failures),
                    "total": len(shipments),
                    "failed": len(failures),
                },
                messages.WARNING,
            )

        filename = f"acs-labels-{timezone.localdate().isoformat()}.pdf"
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["Content-Length"] = str(len(pdf_bytes))
        return response

    @action(
        description=_("Re-poll tracking for selected shipments"),
        permissions=["change"],
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
        permissions=["change"],
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
        for shipment in queryset.exclude(shipment_state__in=TERMINAL_STATES):
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

    @admin.display(description=_("Timeline"))
    def diagnostics_timeline(self, obj: AcsShipment) -> str:
        """Voucher, label, pickup-list runs, tracking and the last ACS
        calls for this voucher, oldest first."""
        if not obj.pk:
            return "-"
        entries = shipment_timeline(obj)
        return format_html(
            '<ul class="space-y-1">{}</ul>',
            format_html_join(
                "",
                "<li><time>{}</time> — <strong>{}</strong>: {}</li>",
                (
                    (
                        timezone.localtime(entry.at).strftime("%Y-%m-%d %H:%M"),
                        entry.kind,
                        entry.text,
                    )
                    for entry in entries
                ),
            ),
        )

    @admin.display(description=_("Order"))
    def order_link(self, obj: AcsShipment) -> str:
        return change_link(
            self.admin_site, Order, obj.order_id, f"#{obj.order_id}"
        )

    @action(
        description=_("Issue ACS pickup list now"),
        permissions=["change"],
        variant=ActionVariant.PRIMARY,
    )
    def issue_pickup_list_now(self, request):
        """Re-run the daily manifest without waiting for 16:30.

        ACS rejects the whole pickup list when any voucher on it is
        unprinted, and its API has no voucher-list parameter, so a
        single late order blocks every other parcel that day. Printing
        the missing label clears it in seconds — this is the button that
        turns that into a same-day fix instead of a wait until tomorrow.
        """
        from shipping_acs.exceptions import AcsError
        from shipping_acs.services import AcsService

        changelist = _back_to_changelist(self)
        try:
            pickup_list = AcsService.issue_daily_pickup_list(
                issued_by_id=request.user.id
                if request.user.is_authenticated
                else None
            )
        except AcsError as exc:
            # Surface ACS's own words: they name what has to be printed.
            self.message_user(
                request,
                _("ACS did not issue the pickup list: %(err)s")
                % {"err": str(exc)},
                messages.ERROR,
            )
            return changelist

        if pickup_list is None:
            self.message_user(
                request,
                _("No vouchers are waiting for a pickup list."),
                messages.INFO,
            )
            return changelist

        self.message_user(
            request,
            _("Pickup list %(no)s issued for %(n)s voucher(s).")
            % {
                "no": pickup_list.pickup_list_no,
                "n": pickup_list.voucher_count,
            },
            messages.SUCCESS,
        )
        return changelist

    @action(
        description=_("Re-poll ACS tracking"),
        permissions=["change"],
        variant=ActionVariant.INFO,
    )
    def repoll_tracking(self, request, object_id):
        from shipping_acs.tasks import poll_acs_tracking_one

        poll_acs_tracking_one.delay(int(object_id))
        messages.info(request, _("Tracking poll dispatched."))
        return _back_to_changelist(self)

    @action(
        description=_("Record COD received outside ACS"),
        permissions=["change"],
        icon="payments",
        dialog=ActionDialog(
            title=_("Record COD received outside ACS"),
            description=_(
                "Use only when the merchant confirms the cash on delivery "
                "reached them by a route ACS never reported. It stops the "
                "unremitted-COD alert for this parcel and marks the order "
                "paid if it is not already. ACS's own payout records are "
                "not touched."
            ),
            form_class=CodReceivedOutsideAcsForm,
        ),
    )
    def record_cod_received_outside_acs(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        from shipping_acs.exceptions import AcsCodSettlementError
        from shipping_acs.services import AcsService

        shipment = AcsShipment.objects.get(pk=object_id)
        try:
            AcsService.record_cod_received_outside_acs(
                shipment,
                user=request.user,
                note=form.cleaned_data["note"],
            )
        except AcsCodSettlementError as exc:
            # The service names the exact reason (not COD, not delivered,
            # ACS already paid, already recorded): say it verbatim.
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(
                request,
                _("COD recorded as received outside ACS."),
                messages.SUCCESS,
            )
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:shipping_acs_acsshipment_change",
                    args=[object_id],
                ),
            }
        )

    @action(
        description=_("Cancel ACS voucher"),
        permissions=["change"],
        icon="cancel",
        variant=ActionVariant.DANGER,
        dialog=ActionDialog(
            title=_("Cancel the ACS voucher"),
            description=_(
                "Cancels only the voucher at ACS; the order stays as it "
                "is. Use it when the parcel data changed (a new address) "
                "and then press “Issue ACS voucher now” for a fresh one. "
                "ACS refuses a voucher already on an issued pickup list."
            ),
            form_class=CancelVoucherForm,
        ),
    )
    def cancel_voucher_only(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        from shipping_acs.exceptions import AcsError
        from shipping_acs.services import AcsService

        shipment = AcsShipment.objects.get(pk=object_id)
        if shipment.shipment_state == AcsShipmentState.CANCELED:
            self.message_user(
                request,
                _("The voucher is already cancelled."),
                messages.WARNING,
            )
        else:
            try:
                AcsService.cancel_voucher(
                    shipment,
                    reason=form.cleaned_data["reason"] or "admin cancel",
                )
            except AcsError as exc:
                # Names the cause (already on a pickup list, ACS refused).
                self.message_user(request, str(exc), messages.ERROR)
            else:
                self.message_user(
                    request,
                    _("The ACS voucher was cancelled."),
                    messages.SUCCESS,
                )
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:shipping_acs_acsshipment_change",
                    args=[object_id],
                ),
            }
        )

    @staticmethod
    def _back_to_shipment(object_id) -> HttpResponse:
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:shipping_acs_acsshipment_change",
                    args=[object_id],
                ),
            }
        )

    @action(
        description=_("Adopt ACS voucher"),
        permissions=["change"],
        icon="link",
        dialog=ActionDialog(
            title=_("Adopt the voucher ACS holds for this order"),
            description=_(
                "Use only when the last voucher request has an unknown "
                "outcome and you found its voucher in the ACS portal. ACS "
                "must confirm the number; no new voucher is requested."
            ),
            form_class=AdoptVoucherForm,
        ),
    )
    def adopt_voucher(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        from shipping_acs.exceptions import AcsError
        from shipping_acs.services import AcsService

        shipment = AcsShipment.objects.get(pk=object_id)
        try:
            AcsService.adopt_voucher(
                shipment,
                form.cleaned_data["voucher_no"],
                user=request.user,
                confirmed_by_hand=form.cleaned_data["confirmed_by_hand"],
            )
        except AcsError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(
                request,
                _("Voucher %(voucher)s adopted.")
                % {"voucher": form.cleaned_data["voucher_no"]},
                messages.SUCCESS,
            )
        return self._back_to_shipment(object_id)

    @action(
        description=_("No voucher at ACS: request again"),
        permissions=["change"],
        icon="refresh",
        variant=ActionVariant.WARNING,
        dialog=ActionDialog(
            title=_("Request a voucher again"),
            description=_(
                "Use only when the last voucher request has an unknown "
                "outcome and the ACS portal shows no voucher for this "
                "order. If ACS did make one, this creates a second "
                "voucher."
            ),
            form_class=RequestVoucherAgainForm,
        ),
    )
    def request_voucher_again(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        from shipping_acs.exceptions import AcsError
        from shipping_acs.services import AcsService

        shipment = AcsShipment.objects.get(pk=object_id)
        try:
            AcsService.request_voucher_again(
                shipment, user=request.user, note=form.cleaned_data["note"]
            )
        except AcsError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(
                request,
                _("A new voucher request was queued."),
                messages.SUCCESS,
            )
        return self._back_to_shipment(object_id)

    @action(
        description=_("No voucher needed: close"),
        permissions=["change"],
        icon="block",
        dialog=ActionDialog(
            title=_("Close the voucher check"),
            description=_(
                "Use it when the order no longer needs a voucher (the order "
                "or its shipment is canceled). Nothing is sent to ACS. If "
                "ACS holds the lost voucher, delete it there and say so."
            ),
            form_class=RequestVoucherAgainForm,
        ),
    )
    def close_voucher_request(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        from shipping_acs.exceptions import AcsError
        from shipping_acs.services import AcsService

        shipment = AcsShipment.objects.get(pk=object_id)
        try:
            AcsService.close_voucher_request(
                shipment, user=request.user, note=form.cleaned_data["note"]
            )
        except AcsError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(
                request, _("The voucher check was closed."), messages.SUCCESS
            )
        return self._back_to_shipment(object_id)

    @action(
        description=_("Issue ACS voucher now"),
        permissions=["change"],
        variant=ActionVariant.PRIMARY,
    )
    def issue_voucher_now(self, request, object_id):
        from shipping_acs.models import AcsShipment as _Shipment
        from shipping_acs.services import AcsService
        from shipping_acs.tasks import create_acs_voucher_for_order

        try:
            shipment = _Shipment.objects.get(pk=object_id)
        except _Shipment.DoesNotExist:
            messages.error(request, _("Shipment not found."))
            return _back_to_changelist(self)

        # The mint itself refuses an unpaid online order
        # (``ShipmentAwaitingPaymentError`` in
        # ``AcsService.create_voucher_for_order``), but it runs in a
        # Celery task, so its refusal never reaches this page. Say so
        # here, before resetting anything, instead of reporting a
        # dispatch that is bound to be refused.
        if shipment.order.awaits_online_payment:
            messages.error(
                request,
                _(
                    "Order #%(order)s has not been paid yet — the voucher "
                    "is issued automatically once the online payment is "
                    "confirmed."
                )
                % {"order": shipment.order_id},
            )
            return _back_to_changelist(self)

        # The mint refuses it too (``AcsVoucherRequestUnknownError``), but
        # in a Celery task whose refusal never reaches this page.
        if voucher_request_unknown(shipment):
            messages.error(request, voucher_check_message(shipment))
            return _back_to_changelist(self)

        # A CANCELED shipment keeps its (now-deleted) voucher_no, which
        # makes the mint task short-circuit and return the dead voucher.
        # Reset it first so a fresh voucher is issued — ACS supports the
        # delete → re-create reissue flow.
        if shipment.shipment_state == AcsShipmentState.CANCELED:
            try:
                AcsService.reset_shipment_for_remint(shipment)
            except AcsVoucherRequestUnknownError:
                # The reset found the last request's outcome unknown,
                # recorded it and reset nothing: nothing is queued.
                shipment.refresh_from_db()
                messages.error(request, voucher_check_message(shipment))
                return _back_to_changelist(self)
            except Exception as exc:
                messages.error(
                    request,
                    _("Cannot reset shipment for re-mint: %(err)s")
                    % {"err": str(exc)},
                )
                return _back_to_changelist(self)
            messages.info(
                request,
                _("Cancelled shipment reset — minting a fresh voucher."),
            )

        # The mint holds the request (``AcsVoucherRequestHeldError``) in a
        # Celery task whose answer never reaches this page, so the page
        # says what the mint's own rule says at the press, read before
        # the task can run: ``holding``, not ``in_force``. A hold that
        # lets one request go and has the turn free does not hold this
        # request; it is the one sent. The state can still change before
        # the task reaches its slot; either way the request is sent by
        # itself, now or once ACS answers again.
        hold = voucher_hold.holding()
        dispatch_on_commit(create_acs_voucher_for_order, [shipment.order_id])
        if hold is not None:
            messages.warning(
                request,
                _(
                    "ACS voucher requests are held since %(since)s because "
                    "ACS is failing. This one is sent automatically once ACS "
                    "answers again."
                )
                % {"since": _local_time(hold.started_at)},
            )
        else:
            messages.info(request, _("Voucher creation task dispatched."))
        return _back_to_changelist(self)


@admin.register(AcsStation)
class AcsStationAdmin(BaseModelAdmin):
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
class AcsPickupListAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    staff_actor_fields = ("issued_by",)
    list_display = (
        "pickup_list_no",
        "issued_at",
        "voucher_count",
        "issued_by",
    )
    date_hierarchy = "created_at"
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
    # Shown read-only as ``issued_by``.
    exclude = ("issued_by_id",)


@admin.register(AcsPickupListRun)
class AcsPickupListRunAdmin(BaseModelAdmin):
    """Every attempt to issue a pickup list and how it ended.

    Readable by the store staff who press "Issue ACS pickup list now":
    the rows hold voucher numbers and ACS's message, nothing personal.
    """

    staff_actor_fields = ("issued_by",)
    list_display = (
        "pickup_date",
        "started_at",
        "outcome",
        "candidate_count",
        "pickup_list",
        "issued_by",
    )
    date_hierarchy = "pickup_date"
    list_filter = ("outcome", ("pickup_date", RangeDateFilter))
    search_fields = (
        "pickup_list__pickup_list_no",
        "acs_message",
        "error_message",
    )
    list_select_related = ("pickup_list",)
    readonly_fields = (
        "pickup_date",
        "started_at",
        "outcome",
        "candidate_vouchers",
        "unprinted_vouchers_acs",
        "unprinted_vouchers_local",
        "acs_message",
        "error_message",
        "pickup_list",
        "issued_by",
        "created_at",
        "updated_at",
    )
    exclude = ("issued_by_id",)

    @admin.display(description=_("Candidates"))
    def candidate_count(self, obj: AcsPickupListRun) -> int:
        return len(obj.candidate_vouchers or [])

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class UnrecognisedVoucherResolvedFilter(DropdownFilter):
    """Open vouchers are what the dashboard counter counts, by the same
    ``Q`` (:func:`shipping_acs.attention.unresolved_unrecognised_voucher_q`)."""

    title = _("Resolution")
    parameter_name = "resolved"

    def lookups(self, request, model_admin):
        return [("no", _("Not resolved")), ("yes", _("Resolved"))]

    def queryset(self, request, queryset):
        match self.value():
            case "no":
                return queryset.filter(unresolved_unrecognised_voucher_q())
            case "yes":
                return queryset.exclude(unresolved_unrecognised_voucher_q())
            case _:
                return queryset


@admin.register(AcsUnrecognisedVoucher)
class AcsUnrecognisedVoucherAdmin(BaseModelAdmin):
    """Vouchers ACS named on a refused pickup list that match no shipment.

    Readable by the store staff who work the ACS shipments: the rows hold
    voucher numbers and staff notes. A voucher is adopted from the
    shipment it belongs to ("Adopt ACS voucher"), deleted at ACS while it
    is on no pickup list, or marked as checked.
    """

    staff_actor_fields = ("resolved_by",)
    list_display = (
        "voucher_no",
        "source",
        "pickup_date",
        "first_seen_at",
        "last_seen_at",
        "resolution",
        "resolved_at",
    )
    list_filter = (UnrecognisedVoucherResolvedFilter, "source", "resolution")
    search_fields = ("voucher_no",)
    readonly_fields = (
        "voucher_no",
        "source",
        "duplicate_of",
        "pickup_date",
        "first_seen_at",
        "last_seen_at",
        "resolution",
        "resolved_at",
        "resolved_by",
        "resolution_note",
        "shipment",
        "created_at",
        "updated_at",
    )
    exclude = ("resolved_by_id",)
    actions_detail = ["delete_at_acs", "mark_checked"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_delete_at_acs_permission(self, request, object_id=None) -> bool:
        """Deleting at ACS cannot be undone, so it asks what a deletion
        asks: the role's ``delete`` on these rows, besides ``change`` for
        resolving the row here. Read from the backend, not through
        ``has_delete_permission``, which refuses everyone because a row
        is resolved, never removed."""
        return self.has_change_permission(request) and request.user.has_perm(
            "shipping_acs.delete_acsunrecognisedvoucher"
        )

    @staticmethod
    def _back_to_voucher(object_id) -> HttpResponse:
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:shipping_acs_acsunrecognisedvoucher_change",
                    args=[object_id],
                ),
            }
        )

    @action(
        description=_("Delete at ACS"),
        permissions=["delete_at_acs"],
        icon="delete",
        variant=ActionVariant.DANGER,
        dialog=ActionDialog(
            title=_("Delete this voucher at ACS"),
            description=_(
                "Deletes the voucher at ACS. ACS allows it only while the "
                "voucher is on no pickup list. It is refused while an order "
                "waits for a voucher whose request had an unknown outcome, "
                "because this may be that order's voucher."
            ),
            form_class=ResolveUnrecognisedVoucherForm,
        ),
    )
    def delete_at_acs(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        from shipping_acs.exceptions import AcsError
        from shipping_acs.services import AcsService

        row = AcsUnrecognisedVoucher.objects.get(pk=object_id)
        try:
            AcsService.delete_unrecognised_voucher(
                row, user=request.user, note=form.cleaned_data["note"]
            )
        except AcsError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(
                request,
                _("Voucher %(voucher)s was deleted at ACS.")
                % {"voucher": row.voucher_no},
                messages.SUCCESS,
            )
        return self._back_to_voucher(object_id)

    @action(
        description=_("Mark as checked"),
        permissions=["change"],
        icon="task_alt",
        dialog=ActionDialog(
            title=_("Mark this voucher as checked"),
            description=_(
                "Use it when you know the voucher (made in the ACS portal, "
                "handled at an ACS store). It stops being counted; nothing "
                "is sent to ACS."
            ),
            form_class=ResolveUnrecognisedVoucherForm,
        ),
    )
    def mark_checked(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        from shipping_acs.exceptions import AcsError
        from shipping_acs.services import AcsService

        row = AcsUnrecognisedVoucher.objects.get(pk=object_id)
        try:
            AcsService.mark_unrecognised_voucher_checked(
                row, user=request.user, note=form.cleaned_data["note"]
            )
        except AcsError as exc:
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(
                request,
                _("Voucher %(voucher)s marked as checked.")
                % {"voucher": row.voucher_no},
                messages.SUCCESS,
            )
        return self._back_to_voucher(object_id)


class VoucherHoldInForceFilter(DropdownFilter):
    """The hold in force is what the dashboard row counts, by the same
    ``Q`` (:func:`shipping_acs.attention.voucher_hold_in_force_q`)."""

    title = _("In force")
    parameter_name = "in_force"

    def lookups(self, request, model_admin):
        return [("yes", _("In force")), ("no", _("Ended"))]

    def queryset(self, request, queryset):
        match self.value():
            case "yes":
                return queryset.filter(voucher_hold_in_force_q())
            case "no":
                return queryset.exclude(voucher_hold_in_force_q())
            case _:
                return queryset


@admin.register(AcsVoucherHold)
class AcsVoucherHoldAdmin(BaseModelAdmin):
    """The spans in which the store's voucher requests waited for ACS.

    A record, never edited: a hold begins with a voucher request whose
    answer was lost and ends when ACS answers one again
    (:mod:`shipping_acs.voucher_hold`). The one thing staff do here is
    say that ACS answers again, which sends one request at once instead
    of after the wait.
    """

    list_display = (
        "started_at",
        "ended_at",
        "state",
        "failed_trials",
        "last_failure_at",
        "reason",
    )
    list_filter = (VoucherHoldInForceFilter,)
    readonly_fields = (
        "started_at",
        "ended_at",
        "state",
        "reason",
        "last_failure_at",
        "failed_trials",
        "asked_again_after",
        "trial_allowed_at",
        "trial_until",
    )
    fields = readonly_fields
    actions_detail = ["allow_one_request"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description=_("State"))
    def state(self, obj: AcsVoucherHold) -> str:
        if obj.ended_at is not None:
            return str(_("Ended: ACS answered"))
        if obj.trial_allowed_at is None:
            return str(_("Held: nothing is sent"))
        if voucher_requests_held().exists():
            return str(_("One request first"))
        return str(_("No request waits: the next one goes alone"))

    @admin.display(description=_("ACS is asked again after"))
    def asked_again_after(self, obj: AcsVoucherHold) -> str:
        if obj.ended_at is not None or obj.trial_allowed_at is not None:
            return "-"
        return _local_time(obj.retry_after)

    @action(
        description=_("ACS answers again: send one request"),
        icon="play_arrow",
        variant=ActionVariant.PRIMARY,
        permissions=["change"],
        dialog=ActionDialog(
            title=_("Send one voucher request to ACS now"),
            description=_(
                "Use it when you know ACS answers again and do not want to "
                "wait. One held voucher request is sent first; the others "
                "follow only after ACS answers it. If that answer is lost "
                "too, that one order has to be checked at the ACS portal "
                "and the rest keep waiting."
            ),
        ),
    )
    def allow_one_request(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        hold = voucher_hold.in_force()
        if hold is None or str(hold.pk) != str(object_id):
            self.message_user(
                request,
                _("This hold is no longer in force."),
                messages.WARNING,
            )
        elif not voucher_hold.allow_trial():
            # Nothing changed, so nothing is logged or dispatched: one
            # request was already allowed (it is out, or goes on the
            # next tick), or the hold ended a moment ago.
            self.message_user(
                request,
                _("This hold is no longer in force.")
                if voucher_hold.in_force() is None
                else _(
                    "One voucher request was already allowed to go first. "
                    "Nothing more was sent."
                ),
                messages.WARNING,
            )
        else:
            self.log_change(
                request,
                hold,
                "Said ACS answers again: one voucher request sent first",
            )
            dispatch_on_commit(issue_held_acs_vouchers)
            self.message_user(
                request,
                _(
                    "One held voucher request is being sent to ACS. The "
                    "others follow once ACS answers it."
                ),
                messages.SUCCESS,
            )
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:shipping_acs_acsvoucherhold_change",
                    args=[object_id],
                ),
            }
        )


@admin.register(AcsApiCall)
class AcsApiCallAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    """Read-only history of every ACS call.

    Superuser only, like the other ACS audit trails: the redacted request
    and response still carry the recipient's name, address and phone.
    """

    list_display = (
        "created_at",
        "alias",
        "outcome",
        "http_status",
        "duration_ms",
        "voucher_no",
        "order_id",
    )
    date_hierarchy = "created_at"
    list_filter = (
        "alias",
        "outcome",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("voucher_no", "reference_no", "error_message")
    readonly_fields = (
        "alias",
        "outcome",
        "http_status",
        "duration_ms",
        "error_message",
        "request_params",
        "response",
        "voucher_no",
        "order_id",
        "pickup_date",
        "reference_no",
        "created_at",
        "updated_at",
    )
    ordering = ("-created_at",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AcsCodPayout)
class AcsCodPayoutAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
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
    date_hierarchy = "cod_payment_date"
    list_filter = (
        UnattributedPayoutFilter,
        ReconciledPayoutFilter,
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
        "row_fingerprint",
        "reconciled_at",
        "reconciled_by",
        "reconciliation_note",
        "raw_payload",
        "created_at",
        "updated_at",
    )
    # Shown read-only as ``reconciled_by``.
    exclude = ("reconciled_by_id",)
    # Reconciles every payout, not a row: a list action, not a row one.
    actions_list = ["run_reconciliation"]
    actions_row = ["mark_reconciled"]
    actions_detail = ["mark_reconciled"]

    @action(
        description=_("Mark as reconciled"),
        permissions=["change"],
        icon="task_alt",
        dialog=ActionDialog(
            title=_("Mark payout as reconciled"),
            description=_(
                "Use once this payout has been accounted for although no "
                "shipment of the store explains it. It stops counting as "
                "needing attention on the dashboard. The payout itself is "
                "kept unchanged."
            ),
            form_class=ReconcilePayoutForm,
        ),
    )
    def mark_reconciled(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        from shipping_acs.exceptions import AcsPayoutReconciliationError
        from shipping_acs.services import AcsService

        payout = AcsCodPayout.objects.get(pk=object_id)
        try:
            AcsService.reconcile_cod_payout(
                payout, user=request.user, note=form.cleaned_data["note"]
            )
        except AcsPayoutReconciliationError as exc:
            # The service names the exact reason (tied to a shipment,
            # already reconciled): say it verbatim.
            self.message_user(request, str(exc), messages.ERROR)
        else:
            self.message_user(
                request, _("Payout marked as reconciled."), messages.SUCCESS
            )
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:shipping_acs_acscodpayout_changelist"
                ),
            }
        )

    @action(
        description=_("Run COD reconciliation now"),
        permissions=["change"],
        variant=ActionVariant.PRIMARY,
    )
    def run_reconciliation(self, request):
        from shipping_acs.tasks import reconcile_acs_cod_payouts

        reconcile_acs_cod_payouts.delay()
        messages.info(request, _("COD reconciliation task dispatched."))
        return _back_to_changelist(self)


@admin.register(AcsTrackingEvent)
class AcsTrackingEventAdmin(IsSuperuserOnlyModelAdmin, BaseModelAdmin):
    """Read-only audit trail of the tracking checkpoints the ACS poll
    recorded (ACS has no webhook)."""

    list_display = (
        "shipment",
        "event_time",
        "checkpoint_action",
        "checkpoint_location",
        "received_at",
    )
    date_hierarchy = "event_time"
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
