from django.contrib.postgres.indexes import BTreeIndex
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

from core.models import TimeStampMixinModel


class AcsApiCallOutcome(models.TextChoices):
    OK = "ok", _("OK")
    BUSINESS_ERROR = "business_error", _("ACS refused")
    RETRYABLE = "retryable", _("Retryable server error")
    AUTH = "auth", _("Auth-layer rejection")
    TRANSPORT = "transport", _("Connection or timeout")
    UNEXPECTED = "unexpected", _("Unexpected failure")


class AcsApiCall(TimeStampMixinModel):
    """One row per ACS RPC call: what we sent and what ACS answered.

    Written by ``AcsClient._call`` (every alias passes through it) so an
    incident can be reconstructed from the admin instead of a production
    shell. Credentials never reach it, PDF/base64 bodies are replaced by a
    length marker and both payloads are size-capped
    (``shipping_acs.observability``). Rows older than
    ``ACS_API_CALL_RETENTION_DAYS`` are swept per tenant.
    """

    alias = models.CharField(_("ACS alias"), max_length=64)
    duration_ms = models.PositiveIntegerField(_("Duration (ms)"))
    http_status = models.PositiveSmallIntegerField(
        _("HTTP status"),
        null=True,
        blank=True,
        help_text=_("Empty when no HTTP response arrived."),
    )
    outcome = models.CharField(
        _("Outcome"), max_length=16, choices=AcsApiCallOutcome.choices
    )
    error_message = models.TextField(
        _("ACS error message"),
        blank=True,
        default="",
        help_text=_(
            "ACSExecutionErrorMessage, or the Error_Message of the value row."
        ),
    )
    request_params = models.JSONField(
        _("Request parameters"), default=dict, blank=True
    )
    response = models.JSONField(
        _("Response"), default=dict, blank=True, null=True
    )
    voucher_no = models.CharField(
        _("Voucher number"), max_length=32, blank=True, default=""
    )
    order_id = models.BigIntegerField(_("Order"), null=True, blank=True)
    pickup_date = models.DateField(_("Pickup date"), null=True, blank=True)
    reference_no = models.CharField(
        _("Reference number"), max_length=64, blank=True, default=""
    )

    class Meta(TypedModelMeta):
        verbose_name = _("ACS API call")
        verbose_name_plural = _("ACS API calls")
        ordering = ["-created_at", "-id"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            BTreeIndex(fields=["alias"], name="acs_apicall_alias_ix"),
            BTreeIndex(fields=["outcome"], name="acs_apicall_outcome_ix"),
            BTreeIndex(fields=["voucher_no"], name="acs_apicall_voucher_ix"),
        ]

    def __str__(self) -> str:
        return f"{self.alias} {self.outcome} @ {self.created_at}"
