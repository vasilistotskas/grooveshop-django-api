from django.conf import settings
from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel


class AcsPickupList(TimeStampMixinModel):
    """One row per daily ACS pickup list (manifest).

    The pickup list is the contract between us and ACS — the courier
    will not collect packages until ``ACS_Issue_Pickup_List`` finalises
    that day's vouchers and returns a ``PickupList_No``.

    Issued automatically by the ``issue_daily_acs_pickup_list`` Celery
    beat task (Mon–Fri 16:30 Europe/Athens) and on demand from admin.

    Once a pickup list is finalised, member vouchers can no longer be
    cancelled via ``ACS_Delete_Voucher`` — that's why
    ``AcsService.cancel_voucher`` guards on ``pickup_list_id IS NULL``.
    """

    pickup_list_no = models.CharField(
        _("Pickup list number"),
        max_length=32,
        unique=True,
        help_text=_("PickupList_No returned by ACS_Issue_Pickup_List."),
    )
    issued_at = models.DateTimeField(_("Issued at"))
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acs_pickup_lists_issued",
        verbose_name=_("Issued by"),
        help_text=_(
            "Admin who triggered the manual issue. Null when the daily "
            "Celery beat task issued the list."
        ),
    )
    billing_code = models.CharField(
        _("Billing code"),
        max_length=32,
        help_text=_(
            "Billing_Code captured at issuance time so historical "
            "manifests stay reprintable even if the env var changes."
        ),
    )
    voucher_count = models.PositiveIntegerField(
        _("Voucher count"),
        default=0,
    )
    metadata = models.JSONField(
        _("Metadata"),
        default=dict,
        blank=True,
        help_text=_(
            "Cached print PDF (base64), unprinted-vouchers list, and "
            "raw API response for debugging."
        ),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("ACS pickup list")
        verbose_name_plural = _("ACS pickup lists")
        ordering = ["-issued_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["pickup_list_no"], name="acs_pickup_list_no_ix"),
            BTreeIndex(fields=["issued_at"], name="acs_pickup_issued_ix"),
        ]

    def __str__(self) -> str:
        return self.pickup_list_no
