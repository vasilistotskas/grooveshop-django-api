import logging
from decimal import Decimal

from django import forms
from django.contrib import admin, messages
from django.db.models import Prefetch, Sum
from django.http import HttpRequest, HttpResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from unfold.contrib.filters.admin import (
    AutocompleteSelectFilter,
    RangeDateTimeFilter,
)
from unfold.dataclasses import ActionDialog
from unfold.datasets import BaseDataset
from unfold.decorators import action, display
from unfold.forms import BaseDialogForm
from unfold.sections import TableSection
from unfold.widgets import (
    UnfoldAdminDecimalFieldWidget,
    UnfoldAdminTextInputWidget,
)

from admin.base import BaseModelAdmin
from admin.datasets import RelatedDatasetAdmin
from admin.displays import money
from admin.export import ExportActionMixin
from giftcard.enum import GiftCardStatus, GiftCardTransactionKind
from giftcard.models import GiftCard, GiftCardPurchase, GiftCardTransaction
from giftcard.services import GiftCardService
from tenant.celery import dispatch_on_commit

logger = logging.getLogger(__name__)


def _over_limit_message():
    return _(
        "This is above the limit a staff member can put on gift cards in "
        "one step. The store's owner can do it."
    )


def _refuse_over_limit(request, amount) -> None:
    logger.warning(
        "Gift-card value above the staff limit refused",
        extra={"user_id": request.user.pk, "amount": str(amount)},
    )


def _check_issue_limit(request, amount: Decimal) -> None:
    if GiftCardService.over_staff_limit(request.user, amount):
        _refuse_over_limit(request, amount)
        raise forms.ValidationError(_over_limit_message())


def _above_limit_note(amount: Decimal) -> str:
    """A ledger note when the owner (or a superuser) put on more than the
    staff limit: who did it is on the row (``created_by``) and in the admin
    log."""
    limit = GiftCardService.staff_issue_limit()
    if limit > 0 and amount > limit:
        return f" [above the staff limit of {limit}, authorised by the owner]"
    return ""


def _issue_description(amount) -> str:
    return str(_("Issued via admin")) + _above_limit_note(Decimal(str(amount)))


class AdjustBalanceForm(BaseDialogForm):
    amount = forms.DecimalField(
        label=_("Adjustment amount"),
        max_digits=11,
        decimal_places=2,
        widget=UnfoldAdminDecimalFieldWidget,
        help_text=_(
            "Positive adds balance, negative removes it (never below "
            "the current balance)"
        ),
    )
    reason = forms.CharField(
        label=_("Reason"),
        max_length=255,
        widget=UnfoldAdminTextInputWidget,
        help_text=_("Recorded on the ledger row"),
    )


class TransactionsTableSection(TableSection):
    verbose_name = _("Ledger")
    height = 300
    related_name = "transactions"
    fields = ["pk", "kind", "amount", "order", "created_at"]


class LedgerDatasetAdmin(RelatedDatasetAdmin):
    parent_field = "gift_card"
    list_display = ("kind", "amount", "order", "created_by", "created_at")
    ordering = ("-created_at",)

    def get_queryset(self, request):
        return (
            super().get_queryset(request).select_related("order", "created_by")
        )


class LedgerDataset(BaseDataset):
    model = GiftCardTransaction
    model_admin = LedgerDatasetAdmin
    title = _("Ledger")
    tab = True


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
    change_form_datasets = [LedgerDataset]
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

    def get_form(self, request, obj=None, change=False, **kwargs):
        form = super().get_form(request, obj, change=change, **kwargs)
        if obj is None:
            # ``get_form`` builds a fresh form class per call, so the
            # validator that closes over this request is this request's.
            form.base_fields["initial_value"].validators.append(
                lambda value: _check_issue_limit(
                    request, Decimal(str(value.amount))
                )
            )
        return form

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
            description=_issue_description(obj.initial_value.amount),
        )
        obj.pk = card.pk
        obj.uuid = card.uuid
        obj.code = card.code

    def get_queryset(self, request):
        """The ledger sum (``GiftCard.balance``) and the ledger section's
        rows, fetched with the page rather than once per card."""
        return (
            super()
            .get_queryset(request)
            .annotate(ledger_total=Sum("transactions__amount"))
            .prefetch_related(
                Prefetch(
                    "transactions",
                    queryset=GiftCardTransaction.objects.select_related(
                        "order"
                    ),
                )
            )
        )

    @display(
        description=_("Balance"), formatting="price", ordering="ledger_total"
    )
    def balance_display(self, obj):
        return money(obj.balance.amount)

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
        dialog=ActionDialog(
            title=_("Adjust gift card balance"),
            description=_(
                "Writes an ADJUST row on the ledger — the card "
                "history stays auditable."
            ),
            form_class=AdjustBalanceForm,
        ),
    )
    def adjust_balance(
        self, request: HttpRequest, form, object_id=None, **kwargs
    ) -> HttpResponse:
        card = GiftCard.objects.get(pk=object_id)
        amount = form.cleaned_data["amount"]
        if amount > 0 and GiftCardService.over_staff_limit(
            request.user, amount
        ):
            _refuse_over_limit(request, amount)
            self.message_user(request, _over_limit_message(), messages.ERROR)
        elif amount < 0 and -amount > Decimal(str(card.balance.amount)):
            self.message_user(
                request,
                _("Cannot remove more than the current balance."),
                messages.ERROR,
            )
        else:
            note = _above_limit_note(amount) if amount > 0 else ""
            GiftCardTransaction.objects.create(
                gift_card=card,
                kind=GiftCardTransactionKind.ADJUST,
                amount=amount,
                created_by=request.user,
                # The note is what records the authorisation, so it is
                # the reason that gives way when the two do not fit.
                description=form.cleaned_data["reason"][: 255 - len(note)]
                + note,
            )
            if note:
                self.log_change(
                    request, card, f"Added {amount} above the staff limit"
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

        from giftcard.tasks import deliver_gift_card_email

        GiftCard.objects.filter(pk=object_id).update(delivered_at=None)
        dispatch_on_commit(deliver_gift_card_email, [object_id])
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
        # Autocomplete, not a dropdown of every card ever issued.
        ("gift_card", AutocompleteSelectFilter),
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
    date_hierarchy = "created_at"
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
