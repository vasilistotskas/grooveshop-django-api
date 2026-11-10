from __future__ import annotations

from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel
from tenant.staff_actors import StaffActor


class AcsUnrecognisedVoucherResolution(models.TextChoices):
    ADOPTED = "adopted", _("Adopted by a shipment")
    DELETED = "deleted", _("Deleted at ACS")
    CHECKED = "checked", _("Checked by staff")


class AcsUnrecognisedVoucher(TimeStampMixinModel):
    """A voucher ACS holds for the store that matches no shipment.

    ``ACS_Issue_Pickup_List`` names every unprinted voucher of the account
    when it refuses the day (manual, April 2026, p. 14). One that is no
    shipment's voucher (nor a child of one) is the possible lost answer of
    a voucher request, a duplicate, or a voucher made in ACS's own portal.
    No documented call ties it back to an order before it is on a pickup
    list, so staff decide; the row stays counted until they do.
    """

    voucher_no = models.CharField(
        _("Voucher number"), max_length=20, unique=True
    )
    pickup_date = models.DateField(
        _("Pickup date"),
        help_text=_("The pickup list run that first named the voucher."),
    )
    first_seen_at = models.DateTimeField(_("First named by ACS at"))
    last_seen_at = models.DateTimeField(_("Last named by ACS at"))
    resolved_at = models.DateTimeField(_("Resolved at"), null=True, blank=True)
    resolution = models.CharField(
        _("Resolution"),
        max_length=16,
        choices=AcsUnrecognisedVoucherResolution.choices,
        blank=True,
        default="",
        db_default="",
    )
    resolved_by_id = models.BigIntegerField(
        _("Resolved by"),
        null=True,
        blank=True,
        help_text=_(
            "Operator who resolved the voucher. Id of a platform staff account (public schema), not a store user. Read it through the matching accessor."
        ),
    )
    resolved_by = StaffActor()
    resolution_note = models.TextField(
        _("Resolution note"),
        blank=True,
        default="",
        db_default="",
    )
    shipment = models.ForeignKey(
        "shipping_acs.AcsShipment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="adopted_unrecognised_vouchers",
        verbose_name=_("Adopted by shipment"),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("ACS voucher matching no order")
        verbose_name_plural = _("ACS vouchers matching no order")
        ordering = ["-last_seen_at"]
        # Explicit short names: the mixin's ``%(class)s_created_at_ix``
        # resolves to ``acsunrecognisedvoucher_created_at_ix`` (36 chars),
        # which trips ``models.E034`` when the tenant migration runs its
        # database checks.
        indexes = [
            BTreeIndex(fields=["created_at"], name="acs_unrec_created_at_ix"),
            BTreeIndex(fields=["updated_at"], name="acs_unrec_updated_at_ix"),
        ]

    def __str__(self) -> str:
        return self.voucher_no
