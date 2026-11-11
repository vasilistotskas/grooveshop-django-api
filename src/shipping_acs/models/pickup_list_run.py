from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel
from tenant.staff_actors import StaffActor


class AcsPickupListRunOutcome(models.TextChoices):
    ISSUED = "issued", _("Pickup list issued")
    ADOPTED = "adopted", _("Pickup list adopted from ACS")
    NOTHING_TO_ISSUE = "nothing_to_issue", _("Nothing to issue")
    BLOCKED_UNPRINTED = "blocked_unprinted", _("Refused: unprinted labels")
    BLOCKED_UNPRINTED_SILENT = (
        "blocked_unprinted_silent",
        _("Refused silently: unprinted labels"),
    )
    ERROR = "error", _("Error")


class AcsPickupListRun(TimeStampMixinModel):
    """One row per attempt to issue a pickup list, whatever came of it.

    ``AcsPickupList`` only exists when a manifest was issued, so "why was
    no list issued on date X" had no answer in the database. This is
    written by ``AcsService.issue_daily_pickup_list`` on every outcome,
    for the 16:30 beat task and the admin "Issue ACS pickup list now"
    action alike.
    """

    pickup_date = models.DateField(_("Pickup date"))
    started_at = models.DateTimeField(_("Started at"))
    outcome = models.CharField(
        _("Outcome"), max_length=32, choices=AcsPickupListRunOutcome.choices
    )
    candidate_vouchers = models.JSONField(
        _("Candidate vouchers"), default=list, blank=True
    )
    unprinted_vouchers_acs = models.JSONField(
        _("Unprinted vouchers named by ACS"), default=list, blank=True
    )
    unprinted_vouchers_local = models.JSONField(
        _("Candidates with no local label_printed_at"),
        default=list,
        blank=True,
    )
    acs_message = models.TextField(_("ACS message"), blank=True, default="")
    error_message = models.TextField(_("Error"), blank=True, default="")
    pickup_list = models.ForeignKey(
        "shipping_acs.AcsPickupList",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="runs",
        verbose_name=_("Pickup list"),
    )
    issued_by_id = models.BigIntegerField(
        _("Triggered by"),
        null=True,
        blank=True,
        help_text=_(
            "Admin who pressed the button; empty for the daily beat task. Id of a platform staff account (public schema), not a store user. Read it through the matching accessor."
        ),
    )
    issued_by = StaffActor()

    class Meta(TypedModelMeta):
        verbose_name = _("ACS pickup list run")
        verbose_name_plural = _("ACS pickup list runs")
        ordering = ["-started_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["pickup_date"], name="acs_run_date_ix"),
            BTreeIndex(fields=["outcome"], name="acs_run_outcome_ix"),
        ]

    def __str__(self) -> str:
        return f"{self.pickup_date} {self.outcome}"
