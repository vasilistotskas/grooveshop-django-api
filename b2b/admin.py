from typing import cast

from django import forms
from django.contrib import admin, messages
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.dataclasses import ActionDialog
from unfold.decorators import action, display
from unfold.forms import BaseDialogForm
from unfold.sections import TableSection

from admin.base import BaseModelAdmin
from admin.export import ExportActionMixin
from b2b.enum import BusinessProfileStatus, ViesStatus
from b2b.models import BusinessProfile, CustomerGroup, PriceListItem
from b2b.services import B2BService


class ApproveForm(BaseDialogForm):
    customer_group = forms.ModelChoiceField(
        label=_("Customer group"),
        queryset=CustomerGroup.objects.filter(is_active=True),
        help_text=_("The wholesale pricing tier this business gets"),
    )


class RejectForm(BaseDialogForm):
    reason = forms.CharField(
        label=_("Reason"),
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("Included in the notification email to the customer"),
    )


class ImportPricesForm(BaseDialogForm):
    lines = forms.CharField(
        label=_("Price lines"),
        widget=forms.Textarea(
            attrs={"rows": 12, "placeholder": "SKU-001;12.50"}
        ),
        help_text=_(
            "One product per line: sku;net price (VAT-exclusive). "
            "Comma also works as separator and as decimal mark. "
            "Existing rows for this group are updated in place."
        ),
    )


class PriceItemsTableSection(TableSection):
    verbose_name = _("Price list")
    height = 300
    related_name = "price_items"
    fields = ["pk", "product", "net_price", "updated_at"]


@admin.register(BusinessProfile)
class BusinessProfileAdmin(BaseModelAdmin):
    list_display = (
        "company_name",
        "vat_id",
        "user",
        "status_label",
        "customer_group",
        "vies_label",
        "created_at",
    )
    list_filter = (
        "status",
        "vies_status",
        ("customer_group", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("company_name", "vat_id", "user__email")
    autocomplete_fields = ("user", "customer_group")
    list_select_related = ("user", "customer_group")
    readonly_fields = (
        "uuid",
        # Status transitions ONLY via the detail actions — they route
        # through B2BService so the notification emails stay
        # deterministic.
        "status",
        "vies_status",
        "vies_checked_at",
        "vies_name",
        "vies_address",
        "vies_error",
        "reviewed_by",
        "reviewed_at",
        "rejection_reason",
        "created_at",
        "updated_at",
    )
    actions_detail = ["approve", "reject", "suspend", "recheck_vies"]
    ordering = ("-created_at",)
    list_per_page = 50

    fieldsets = (
        (
            _("Company"),
            {
                "classes": ("tab",),
                "fields": (
                    "user",
                    "company_name",
                    "vat_id",
                    "tax_office",
                    "activity",
                    "billing_street",
                    "billing_street_number",
                    "billing_city",
                    "billing_zipcode",
                ),
            },
        ),
        (
            _("Review"),
            {
                "classes": ("tab",),
                "fields": (
                    "status",
                    "customer_group",
                    "reviewed_by",
                    "reviewed_at",
                    "rejection_reason",
                ),
            },
        ),
        (
            _("VIES"),
            {
                "classes": ("tab",),
                "fields": (
                    "vies_status",
                    "vies_checked_at",
                    "vies_name",
                    "vies_address",
                    "vies_error",
                ),
            },
        ),
    )

    @display(
        description=_("Status"),
        label={
            BusinessProfileStatus.PENDING: "warning",
            BusinessProfileStatus.APPROVED: "success",
            BusinessProfileStatus.REJECTED: "danger",
            BusinessProfileStatus.SUSPENDED: "danger",
        },
    )
    def status_label(self, obj):
        return obj.status, obj.get_status_display()

    @display(
        description=_("VIES"),
        label={
            ViesStatus.UNCHECKED: "info",
            ViesStatus.VALID: "success",
            ViesStatus.INVALID: "danger",
            ViesStatus.UNAVAILABLE: "warning",
        },
    )
    def vies_label(self, obj):
        return obj.vies_status, obj.get_vies_status_display()

    @action(
        description=_("Approve"),
        icon="verified",
        dialog=cast(
            "ActionDialog",
            {
                "title": _("Approve business profile"),
                "description": _(
                    "Grants wholesale pricing for the chosen group and "
                    "emails the customer."
                ),
                "form_class": ApproveForm,
                "form_submit_text": None,
            },
        ),
    )
    def approve(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        profile = BusinessProfile.objects.get(pk=object_id)
        B2BService.approve(
            profile,
            group=form.cleaned_data["customer_group"],
            reviewed_by=request.user,
        )
        self.message_user(
            request, _("Business profile approved."), messages.SUCCESS
        )
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:b2b_businessprofile_change", args=[object_id]
                ),
            }
        )

    @action(
        description=_("Reject"),
        icon="block",
        dialog=cast(
            "ActionDialog",
            {
                "title": _("Reject business profile"),
                "description": _(
                    "The reason is included in the email to the customer."
                ),
                "form_class": RejectForm,
                "form_submit_text": None,
            },
        ),
    )
    def reject(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        profile = BusinessProfile.objects.get(pk=object_id)
        B2BService.reject(
            profile,
            reason=form.cleaned_data["reason"],
            reviewed_by=request.user,
        )
        self.message_user(
            request, _("Business profile rejected."), messages.SUCCESS
        )
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:b2b_businessprofile_change", args=[object_id]
                ),
            }
        )

    @action(description=_("Suspend"), icon="pause_circle")
    def suspend(self, request: HttpRequest, object_id: int) -> HttpResponse:
        from django.shortcuts import redirect

        profile = BusinessProfile.objects.get(pk=object_id)
        B2BService.suspend(profile, reviewed_by=request.user)
        self.message_user(
            request, _("Business profile suspended."), messages.SUCCESS
        )
        return redirect(
            reverse("admin:b2b_businessprofile_change", args=[object_id])
        )

    @action(description=_("Re-check VIES"), icon="sync")
    def recheck_vies(
        self, request: HttpRequest, object_id: int
    ) -> HttpResponse:
        from django.shortcuts import redirect

        profile = BusinessProfile.objects.get(pk=object_id)
        B2BService.recheck_vies(profile)
        self.message_user(request, _("VIES re-checked."), messages.SUCCESS)
        return redirect(
            reverse("admin:b2b_businessprofile_change", args=[object_id])
        )


@admin.register(CustomerGroup)
class CustomerGroupAdmin(BaseModelAdmin):
    list_display = (
        "name",
        "discount_percent",
        "min_order_value",
        "is_active",
        "profiles_count",
        "created_at",
    )
    list_filter = ("is_active",)
    search_fields = ("name",)
    list_sections = [PriceItemsTableSection]
    readonly_fields = ("uuid", "created_at", "updated_at")
    actions_detail = ["import_prices"]
    ordering = ("name",)

    @display(description=_("Businesses"))
    def profiles_count(self, obj):
        return obj.business_profiles.count()

    @action(
        description=_("Import prices"),
        icon="upload",
        dialog=cast(
            "ActionDialog",
            {
                "title": _("Import price list"),
                "description": _(
                    "Paste one product per line as sku;net price. "
                    "Rows are created or updated for THIS group only."
                ),
                "form_class": ImportPricesForm,
                "form_submit_text": None,
            },
        ),
    )
    def import_prices(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        group = CustomerGroup.objects.get(pk=object_id)
        summary = B2BService.import_price_lines(
            group, form.cleaned_data["lines"]
        )
        self.message_user(
            request,
            _("Imported: {created} created, {updated} updated.").format(
                created=summary["created"], updated=summary["updated"]
            ),
            messages.SUCCESS,
        )
        for error in summary["errors"][:20]:
            self.message_user(request, error, messages.WARNING)
        if len(summary["errors"]) > 20:
            self.message_user(
                request,
                _("…and {count} more lines with errors.").format(
                    count=len(summary["errors"]) - 20
                ),
                messages.WARNING,
            )
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:b2b_customergroup_change", args=[object_id]
                ),
            }
        )


@admin.register(PriceListItem)
class PriceListItemAdmin(ExportActionMixin, BaseModelAdmin):
    actions = ["export_csv", "export_xml"]

    list_display = ("group", "product", "net_price", "updated_at")
    list_filter = (
        ("group", RelatedDropdownFilter),
        ("updated_at", RangeDateTimeFilter),
    )
    search_fields = ("product__translations__name", "product__sku")
    autocomplete_fields = ("group", "product")
    list_select_related = ("group", "product")
    readonly_fields = ("uuid", "created_at", "updated_at")
    ordering = ("-updated_at",)
    list_per_page = 50
