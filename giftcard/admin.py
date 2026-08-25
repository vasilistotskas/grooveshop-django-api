from decimal import Decimal
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
from giftcard.enum import GiftCardStatus, GiftCardTransactionKind
from giftcard.models import GiftCard, GiftCardPurchase, GiftCardTransaction


class AdjustBalanceForm(BaseDialogForm):
    amount = forms.DecimalField(
        label=_("Adjustment amount"),
        max_digits=11,
        decimal_places=2,
        help_text=_(
            "Positive adds balance, negative removes it (never below "
            "the current balance)"
        ),
    )
    reason = forms.CharField(
        label=_("Reason"),
        max_length=255,
        help_text=_("Recorded on the ledger row"),
    )


class TransactionsTableSection(TableSection):
    verbose_name = _("Ledger")
    height = 300
    related_name = "transactions"
    fields = ["pk", "kind", "amount", "order", "created_at"]


@admin.register(GiftCard)
class GiftCardAdmin(BaseModelAdmin):
    list_display = (
        "code",
        "balance_display",
        "initial_value",
        "status_label",
        "source",
        "recipient_email",
        "expires_at",
        "delivered_at",
    )
    list_filter = (
        "status",
        "source",
        ("expires_at", RangeDateTimeFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("code", "recipient_email", "issued_to__email")
    autocomplete_fields = ("issued_to",)
    list_select_related = ("issued_to",)
    list_sections = [TransactionsTableSection]
    readonly_fields = (
        "uuid",
        "code",
        "source",
        "purchase",
        "delivered_at",
        "created_at",
        "updated_at",
    )
    actions_detail = ["adjust_balance", "disable_card", "resend_delivery"]
    ordering = ("-created_at",)
    list_per_page = 50

    fieldsets = (
        (
            _("Card"),
            {
                "classes": ("tab",),
                "fields": (
                    "code",
                    "initial_value",
                    "status",
                    "expires_at",
                    "source",
                    "purchase",
                    "issued_to",
                ),
            },
        ),
        (
            _("Delivery"),
            {
                "classes": ("tab",),
                "fields": (
                    "recipient_email",
                    "recipient_name",
                    "sender_name",
                    "message",
                    "deliver_at",
                    "delivered_at",
                ),
            },
        ),
    )

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return
        # Admin-issued card: route through the service so the code and
        # the ISSUE ledger row are minted consistently.
        from giftcard.services import GiftCardService

        card = GiftCardService.issue(
            obj.initial_value,
            issued_to=obj.issued_to,
            recipient_email=obj.recipient_email,
            recipient_name=obj.recipient_name,
            sender_name=obj.sender_name,
            message=obj.message,
            deliver_at=obj.deliver_at,
            expires_at=obj.expires_at,
            created_by=request.user,
            description="Issued via admin",
        )
        obj.pk = card.pk
        obj.uuid = card.uuid
        obj.code = card.code

    @display(description=_("Balance"))
    def balance_display(self, obj):
        return obj.balance

    @display(
        description=_("Status"),
        label={
            GiftCardStatus.ACTIVE: "success",
            GiftCardStatus.DISABLED: "danger",
        },
    )
    def status_label(self, obj):
        if obj.status == GiftCardStatus.ACTIVE and obj.is_expired:
            return GiftCardStatus.DISABLED, _("Expired")
        return obj.status, obj.get_status_display()

    @action(
        description=_("Adjust balance"),
        icon="tune",
        dialog=cast(
            "ActionDialog",
            {
                "title": _("Adjust gift card balance"),
                "description": _(
                    "Writes an ADJUST row on the ledger — the card "
                    "history stays auditable."
                ),
                "form_class": AdjustBalanceForm,
                "form_submit_text": None,
            },
        ),
    )
    def adjust_balance(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        card = GiftCard.objects.get(pk=object_id)
        amount = form.cleaned_data["amount"]
        if amount < 0 and -amount > Decimal(str(card.balance.amount)):
            self.message_user(
                request,
                _("Cannot remove more than the current balance."),
                messages.ERROR,
            )
        else:
            GiftCardTransaction.objects.create(
                gift_card=card,
                kind=GiftCardTransactionKind.ADJUST,
                amount=amount,
                created_by=request.user,
                description=form.cleaned_data["reason"],
            )
            self.message_user(request, _("Balance adjusted."), messages.SUCCESS)
        return HttpResponse(
            headers={
                "HX-Redirect": reverse(
                    "admin:giftcard_giftcard_change", args=[object_id]
                ),
            }
        )

    @action(description=_("Disable card"), icon="block")
    def disable_card(
        self, request: HttpRequest, object_id: int
    ) -> HttpResponse:
        GiftCard.objects.filter(pk=object_id).update(
            status=GiftCardStatus.DISABLED
        )
        self.message_user(request, _("Card disabled."), messages.SUCCESS)
        from django.shortcuts import redirect

        return redirect(
            reverse("admin:giftcard_giftcard_change", args=[object_id])
        )

    @action(description=_("Resend delivery email"), icon="forward_to_inbox")
    def resend_delivery(
        self, request: HttpRequest, object_id: int
    ) -> HttpResponse:
        from django.db import connection, transaction

        from giftcard.tasks import deliver_gift_card_email

        GiftCard.objects.filter(pk=object_id).update(delivered_at=None)
        schema = connection.schema_name
        transaction.on_commit(
            lambda: deliver_gift_card_email.apply_async(
                args=[object_id], headers={"_schema_name": schema}
            )
        )
        self.message_user(
            request, _("Delivery email queued."), messages.SUCCESS
        )
        from django.shortcuts import redirect

        return redirect(
            reverse("admin:giftcard_giftcard_change", args=[object_id])
        )


@admin.register(GiftCardTransaction)
class GiftCardTransactionAdmin(ExportActionMixin, BaseModelAdmin):
    actions = ["export_csv", "export_xml"]

    list_display = (
        "gift_card",
        "kind",
        "amount",
        "order",
        "created_by",
        "created_at",
    )
    list_filter = (
        "kind",
        ("gift_card", RelatedDropdownFilter),
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("gift_card__code", "description")
    list_select_related = ("gift_card", "order", "created_by")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GiftCardPurchase)
class GiftCardPurchaseAdmin(BaseModelAdmin):
    list_display = (
        "uuid",
        "amount",
        "buyer_email",
        "recipient_email",
        "status",
        "created_at",
    )
    list_filter = (
        "status",
        ("created_at", RangeDateTimeFilter),
    )
    search_fields = ("uuid", "buyer_email", "recipient_email", "payment_id")
    ordering = ("-created_at",)
    list_per_page = 50

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
