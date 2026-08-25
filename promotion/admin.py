import secrets
from typing import cast

from django import forms
from django.contrib import admin, messages
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from unfold.dataclasses import ActionDialog
from unfold.contrib.filters.admin import (
    RangeDateTimeFilter,
    RelatedDropdownFilter,
)
from unfold.decorators import action, display
from unfold.forms import BaseDialogForm
from unfold.sections import TableSection

from admin.base import BaseModelAdmin, BaseTranslatableAdmin
from admin.export import ExportActionMixin
from promotion.enum import BenefitType, TargetScope
from promotion.models import (
    Promotion,
    PromotionCode,
    PromotionRedemption,
)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I


def _generate_code(prefix: str, length: int) -> str:
    random_part = "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))
    return f"{prefix}{random_part}" if prefix else random_part


class GenerateCodesForm(BaseDialogForm):
    count = forms.IntegerField(
        label=_("Number of codes"), min_value=1, max_value=10000, initial=100
    )
    prefix = forms.CharField(
        label=_("Prefix"),
        max_length=12,
        required=False,
        help_text=_("Optional prefix, e.g. VIP- (uppercased)"),
    )
    length = forms.IntegerField(
        label=_("Random part length"),
        min_value=6,
        max_value=24,
        initial=10,
    )
    usage_limit = forms.IntegerField(
        label=_("Usage limit per code"),
        min_value=1,
        required=False,
        initial=1,
        help_text=_("Empty for unlimited; 1 for single-use codes"),
    )

    def clean_prefix(self):
        return (self.cleaned_data.get("prefix") or "").strip().upper()


class RedemptionsTableSection(TableSection):
    verbose_name = _("Latest redemptions")
    height = 300
    related_name = "redemptions"
    fields = ["pk", "order", "user", "email", "amount", "created_at"]


@admin.register(Promotion)
class PromotionAdmin(BaseTranslatableAdmin):
    list_display = (
        "promotion_name",
        "trigger",
        "benefit_display",
        "show_status",
        "starts_at",
        "ends_at",
        "redemptions_count",
    )
    list_filter = (
        "trigger",
        "benefit_type",
        "target_scope",
        "is_active",
        "stackable",
        ("starts_at", RangeDateTimeFilter),
        ("ends_at", RangeDateTimeFilter),
    )
    search_fields = ("translations__name", "codes__code")
    autocomplete_fields = ("products", "categories")
    list_sections = [RedemptionsTableSection]
    actions = ["activate_promotions", "deactivate_promotions"]
    actions_detail = ["generate_codes", "duplicate_promotion"]

    fieldsets = (
        (
            _("General"),
            {
                "classes": ("tab",),
                "fields": (
                    "name",
                    "description",
                    "trigger",
                    "benefit_type",
                    "benefit_value",
                    "is_active",
                    "starts_at",
                    "ends_at",
                ),
            },
        ),
        (
            _("Targeting"),
            {
                "classes": ("tab",),
                "fields": (
                    "target_scope",
                    "products",
                    "categories",
                ),
            },
        ),
        (
            _("Conditions & Limits"),
            {
                "classes": ("tab",),
                "fields": (
                    "min_subtotal",
                    "first_order_only",
                    "stackable",
                    "priority",
                    "max_discount_amount",
                    "usage_limit_total",
                    "usage_limit_per_customer",
                ),
            },
        ),
    )

    conditional_fields = {
        "benefit_value": f"benefit_type != '{BenefitType.FREE_SHIPPING}'",
        "products": f"target_scope == '{TargetScope.PRODUCTS}'",
        "categories": f"target_scope == '{TargetScope.CATEGORIES}'",
    }

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(redemptions_total=Count("redemptions", distinct=True))
        )

    @display(description=_("Name"), header=True)
    def promotion_name(self, obj):
        return [
            obj.safe_translation_getter("name", any_language=True) or "—",
            obj.get_target_scope_display(),
        ]

    @display(description=_("Benefit"))
    def benefit_display(self, obj):
        if obj.benefit_type == BenefitType.PERCENTAGE:
            return f"-{obj.benefit_value}%"
        if obj.benefit_type == BenefitType.FIXED_AMOUNT:
            return f"-{obj.benefit_value} €"
        return obj.get_benefit_type_display()

    @display(
        description=_("Status"),
        label={
            "live": "success",
            "scheduled": "info",
            "ended": "warning",
            "draft": "danger",
        },
    )
    def show_status(self, obj):
        from django.utils import timezone

        if not obj.is_active:
            return "draft", _("Draft")
        now = timezone.now()
        if obj.starts_at and obj.starts_at > now:
            return "scheduled", _("Scheduled")
        if obj.ends_at and obj.ends_at <= now:
            return "ended", _("Ended")
        return "live", _("Live")

    @display(description=_("Redemptions"), ordering="redemptions_total")
    def redemptions_count(self, obj):
        return obj.redemptions_total

    @admin.action(description=_("Activate selected promotions"))
    def activate_promotions(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(
            request,
            _("%(count)d promotions activated.") % {"count": updated},
            messages.SUCCESS,
        )

    @admin.action(description=_("Deactivate selected promotions"))
    def deactivate_promotions(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(
            request,
            _("%(count)d promotions deactivated.") % {"count": updated},
            messages.SUCCESS,
        )

    @action(
        description=_("Generate codes"),
        icon="confirmation_number",
        # cast: unfold's ActionDialog TypedDict declares plain ``str``
        # keys, but every unfold example (and its own templates) feeds
        # lazy strings through — evaluating them at import time would
        # freeze the admin locale instead.
        dialog=cast(
            "ActionDialog",
            {
                "title": _("Bulk-generate coupon codes"),
                "description": _(
                    "Creates unique random codes attached to this promotion."
                ),
                "form_class": GenerateCodesForm,
                "form_submit_text": None,
            },
        ),
    )
    def generate_codes(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        # Dialog actions are invoked as func(request, form, object_id=…)
        # and must answer with an HX-Redirect header — HTMX submits the
        # dialog form and follows the redirect client-side.
        promotion = Promotion.objects.get(pk=object_id)
        data = form.cleaned_data
        existing = set(PromotionCode.objects.values_list("code", flat=True))
        new_codes: list[PromotionCode] = []
        seen: set[str] = set()
        while len(new_codes) < data["count"]:
            code = _generate_code(data["prefix"], data["length"])
            if code in existing or code in seen:
                continue
            seen.add(code)
            new_codes.append(
                PromotionCode(
                    promotion=promotion,
                    code=code,
                    usage_limit=data.get("usage_limit"),
                )
            )
        PromotionCode.objects.bulk_create(new_codes)
        self.message_user(
            request,
            _("%(count)d codes generated for %(name)s.")
            % {"count": len(new_codes), "name": promotion},
            messages.SUCCESS,
        )
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:promotion_promotion_change", args=[object_id]
                ),
            }
        )

    @action(description=_("Duplicate"), icon="content_copy")
    def duplicate_promotion(
        self, request: HttpRequest, object_id: int
    ) -> HttpResponse:
        source = Promotion.objects.prefetch_related(
            "translations", "products", "categories"
        ).get(pk=object_id)
        clone = Promotion.objects.create(
            trigger=source.trigger,
            benefit_type=source.benefit_type,
            benefit_value=source.benefit_value,
            target_scope=source.target_scope,
            min_subtotal=source.min_subtotal,
            first_order_only=source.first_order_only,
            is_active=False,
            starts_at=source.starts_at,
            ends_at=source.ends_at,
            stackable=source.stackable,
            priority=source.priority,
            max_discount_amount=source.max_discount_amount,
            usage_limit_total=source.usage_limit_total,
            usage_limit_per_customer=source.usage_limit_per_customer,
        )
        for translation in source.translations.all():
            clone.translations.create(
                language_code=translation.language_code,
                name=f"{translation.name} (copy)",
                description=translation.description,
            )
        clone.products.set(source.products.all())
        clone.categories.set(source.categories.all())
        self.message_user(
            request,
            _("Promotion duplicated as an inactive draft."),
            messages.SUCCESS,
        )
        return redirect(
            reverse("admin:promotion_promotion_change", args=[clone.pk])
        )


@admin.register(PromotionCode)
class PromotionCodeAdmin(BaseModelAdmin):
    list_display = (
        "code",
        "promotion",
        "usage_limit",
        "assigned_to",
        "assigned_to_email",
        "is_active",
        "created_at",
    )
    list_filter = (
        "is_active",
        ("promotion", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("code", "assigned_to__email", "assigned_to_email")
    autocomplete_fields = ("promotion", "assigned_to")
    list_select_related = ("promotion", "assigned_to")
    ordering = ("-created_at",)
    list_per_page = 50


@admin.register(PromotionRedemption)
class PromotionRedemptionAdmin(ExportActionMixin, BaseModelAdmin):
    actions = ["export_csv", "export_xml"]

    list_display = (
        "promotion",
        "code",
        "order",
        "user",
        "email",
        "amount",
        "created_at",
    )
    list_filter = (
        ("promotion", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("email", "user__email", "code__code")
    list_select_related = ("promotion", "code", "order", "user")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
