from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from b2b.enum import BusinessProfileStatus, ViesStatus
from b2b.validators import validate_greek_vat
from core.models import TimeStampMixinModel, UUIDModel


class BusinessProfile(TimeStampMixinModel, UUIDModel):
    """A customer's business identity for the wholesale program.

    Tenant-schema table FK'ing the shared user table — a plain FK, the
    constraint crosses into the public schema fine (the
    ``loyalty.PointsTransaction.user`` precedent; ``db_constraint=False``
    is only needed in the shared→tenant direction). OneToOne = one
    profile per user per store.

    Status transitions happen ONLY through ``B2BService`` (admin detail
    actions) so the notification emails stay deterministic.
    """

    id = models.BigAutoField(primary_key=True)
    user = models.OneToOneField(
        "user.UserAccount",
        related_name="business_profile",
        on_delete=models.CASCADE,
    )
    status = models.CharField(
        _("Status"),
        max_length=10,
        choices=BusinessProfileStatus,
        default=BusinessProfileStatus.PENDING,
    )
    customer_group = models.ForeignKey(
        "b2b.CustomerGroup",
        related_name="business_profiles",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    # Company identity — the Greek invoice requisites.
    company_name = models.CharField(_("Company name"), max_length=255)
    vat_id = models.CharField(
        _("VAT number (ΑΦΜ)"),
        max_length=12,
        validators=[validate_greek_vat],
        help_text=_("Stored normalised: 9 digits, no EL/GR prefix"),
    )
    tax_office = models.CharField(_("Tax office (ΔΟΥ)"), max_length=100)
    activity = models.CharField(_("Business activity"), max_length=255)
    billing_street = models.CharField(
        _("Billing street"), max_length=255, blank=True, default=""
    )
    billing_street_number = models.CharField(
        _("Billing street number"), max_length=50, blank=True, default=""
    )
    billing_city = models.CharField(
        _("Billing city"), max_length=100, blank=True, default=""
    )
    billing_zipcode = models.CharField(
        _("Billing zipcode"), max_length=20, blank=True, default=""
    )

    # VIES snapshot — frozen at submit / recheck, shown to the reviewer.
    vies_status = models.CharField(
        _("VIES status"),
        max_length=12,
        choices=ViesStatus,
        default=ViesStatus.UNCHECKED,
    )
    vies_checked_at = models.DateTimeField(
        _("VIES checked at"), null=True, blank=True
    )
    vies_name = models.CharField(
        _("VIES name"), max_length=255, blank=True, default=""
    )
    vies_address = models.CharField(
        _("VIES address"), max_length=255, blank=True, default=""
    )
    vies_error = models.CharField(
        _("VIES error"), max_length=255, blank=True, default=""
    )

    # Review workflow.
    reviewed_by = models.ForeignKey(
        "user.UserAccount",
        related_name="business_profiles_reviewed",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )
    reviewed_at = models.DateTimeField(_("Reviewed at"), null=True, blank=True)
    rejection_reason = models.TextField(
        _("Rejection reason"), blank=True, default=""
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Business Profile")
        verbose_name_plural = _("Business Profiles")
        ordering = ["-created_at"]
        db_table = "b2b_business_profile"
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["status"], name="b2b_profile_status_ix"),
            # NOT unique — several buyer accounts of one company are
            # legitimate; the admin dedupes via search when it matters.
            BTreeIndex(fields=["vat_id"], name="b2b_profile_vat_ix"),
        ]

    def __str__(self):
        return f"{self.company_name} ({self.vat_id})"

    @property
    def is_approved(self) -> bool:
        return self.status == BusinessProfileStatus.APPROVED
