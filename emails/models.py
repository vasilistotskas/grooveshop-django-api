from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from simple_history.models import HistoricalRecords

from core.models import TimeStampMixinModel, UUIDModel
from emails.registry import UnknownEmailType, registry
from emails.status import Status
from emails.types import KEY_MAX_LENGTH, SUBJECT_MAX_LENGTH
from tenant.staff_actors import (
    StaffActor,
    history_user_getter,
    history_user_setter,
)

#: The ``EmailOptOut.type_key`` that covers every marketing type.
EVERY_MARKETING_TYPE = "*"

EMAIL_TYPE_REQUIRED = "email_type_required"
UNKNOWN_EMAIL_TYPE = "unknown_email_type"


class EmailSwitch(TimeStampMixinModel):
    """An email type this store turned off.

    Sparse: a type with no row is on, so a new type needs no data
    migration and "on" is never stored. Written only through
    ``emails.services.set_switch``; a required type has no row, ever.
    """

    type_key = models.CharField(
        _("Email type"), max_length=KEY_MAX_LENGTH, unique=True
    )
    changed_by_staff_id = models.BigIntegerField(
        _("Changed by (staff)"),
        null=True,
        blank=True,
        help_text=_(
            "Id of the platform staff member who switched this type off "
            "(public schema, not a store user). Read it through "
            "``changed_by_staff``."
        ),
    )
    changed_by_staff = StaffActor()

    history = HistoricalRecords(
        history_user_id_field=models.BigIntegerField(null=True),
        history_user_getter=history_user_getter,
        history_user_setter=history_user_setter,
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Email switch")
        verbose_name_plural = _("Email switches")
        ordering = ["type_key"]
        indexes = [*TimeStampMixinModel.Meta.indexes]

    def __str__(self) -> str:
        return self.type_key

    def clean(self) -> None:
        try:
            email_type = registry.get(self.type_key)
        except UnknownEmailType:
            raise ValidationError(
                {
                    "type_key": ValidationError(
                        _("This email type does not exist."),
                        code=UNKNOWN_EMAIL_TYPE,
                    )
                }
            ) from None
        if not email_type.switchable:
            raise ValidationError(
                {
                    "type_key": ValidationError(
                        _("This email is required and cannot be switched off."),
                        code=EMAIL_TYPE_REQUIRED,
                    )
                }
            )


class EmailOptOut(TimeStampMixinModel):
    """A recipient who no longer wants one marketing type, or all of them.

    Keyed by a hash of the address (``emails.optout.address_hash``), so the
    table holds no address and survives the person's account.
    """

    class Source(models.TextChoices):
        LINK = "link", _("Unsubscribe link")
        ONE_CLICK = "one_click", _("One-click unsubscribe")
        ACCOUNT = "account", _("Account preferences")
        STAFF = "staff", _("Store staff")
        COMPLAINT = "complaint", _("Spam complaint")

    address_hash = models.CharField(_("Address hash"), max_length=64)
    type_key = models.CharField(
        _("Email type"),
        max_length=KEY_MAX_LENGTH,
        help_text=_("An email type, or * for every marketing type."),
    )
    source = models.CharField(
        _("Source"), max_length=16, choices=Source.choices
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Email opt-out")
        verbose_name_plural = _("Email opt-outs")
        constraints = [
            models.UniqueConstraint(
                fields=["address_hash", "type_key"],
                name="emails_optout_unique_address_type",
            )
        ]
        indexes = [*TimeStampMixinModel.Meta.indexes]

    def __str__(self) -> str:
        return self.type_key


class EmailProvider(models.TextChoices):
    """The transport that carried a message."""

    SMTP = "smtp", _("SMTP")
    RESEND = "resend", _("Resend")


class EmailLog(TimeStampMixinModel, UUIDModel):
    """One recipient's delivery of one email: sent, skipped or failed.

    Written by ``send_email`` (``emails.log``) in the schema the mail is
    sent under, so a store's rows are in its schema and a platform type's
    in ``public``. The row holds no address (a keyed hash and a masked form
    only) and no body: a body is re-rendered from the type and its object.
    The ``uuid`` is the idempotency key and the hidden tag the provider
    carries, which is how a provider event finds its row.
    """

    type_key = models.CharField(_("Email type"), max_length=KEY_MAX_LENGTH)
    status = models.CharField(
        _("Status"), max_length=24, choices=Status.choices
    )
    reason = models.CharField(
        _("Reason"),
        max_length=120,
        blank=True,
        help_text=_("The closed gate's label, for a skipped feature."),
    )
    recipient_hash = models.CharField(
        _("Recipient hash"),
        max_length=64,
        blank=True,
        help_text=_(
            "Keyed hash of the address; empty when the mail had no "
            "recipient to write to."
        ),
    )
    recipient_masked = models.CharField(
        _("Recipient"), max_length=120, blank=True
    )
    language = models.CharField(_("Language"), max_length=8, blank=True)
    subject = models.CharField(
        _("Subject"), max_length=SUBJECT_MAX_LENGTH, blank=True
    )
    provider = models.CharField(
        _("Provider"), max_length=16, choices=EmailProvider.choices, blank=True
    )
    provider_message_id = models.CharField(
        _("Provider message id"),
        max_length=64,
        null=True,
        blank=True,
        unique=True,
        help_text=_(
            "The id the provider returned. Empty over SMTP, which returns none."
        ),
    )
    error = models.TextField(
        _("Error"),
        blank=True,
        help_text=_("The failure's class and code; never the address."),
    )
    sent_at = models.DateTimeField(_("Sent at"), null=True, blank=True)
    last_event_at = models.DateTimeField(
        _("Last event at"), null=True, blank=True
    )
    is_test = models.BooleanField(
        _("Test send"),
        default=False,
        db_default=False,
        help_text=_("Sent from the admin's Send test, to the staff member."),
    )
    triggered_by_staff_id = models.BigIntegerField(
        _("Triggered by (staff)"),
        null=True,
        blank=True,
        help_text=_(
            "Id of the platform staff member who sent this test (public "
            "schema, not a store user). Read it through "
            "``triggered_by_staff``."
        ),
    )
    triggered_by_staff = StaffActor()

    class Meta(TypedModelMeta):
        verbose_name = _("Email log")
        verbose_name_plural = _("Email logs")
        ordering = ["-created_at"]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            models.Index(
                fields=["type_key", "created_at"], name="emaillog_type_ix"
            ),
            models.Index(
                fields=["status", "created_at"], name="emaillog_status_ix"
            ),
            models.Index(fields=["recipient_hash"], name="emaillog_rcpt_ix"),
        ]

    def __str__(self) -> str:
        return f"{self.type_key} {self.status}"


class EmailEvent(models.Model):
    """One provider event on an :class:`EmailLog` row (the webhook's)."""

    log = models.ForeignKey(
        EmailLog,
        on_delete=models.CASCADE,
        related_name="events",
        verbose_name=_("Email log"),
    )
    provider_event_id = models.CharField(
        _("Provider event id"), max_length=128, unique=True
    )
    type = models.CharField(_("Event"), max_length=64)
    occurred_at = models.DateTimeField(_("Occurred at"))
    detail = models.JSONField(
        _("Detail"),
        default=dict,
        blank=True,
        help_text=_("The bounce type and sub-type; never an address."),
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Email event")
        verbose_name_plural = _("Email events")
        ordering = ["occurred_at"]

    def __str__(self) -> str:
        return self.type


class EmailSuppression(TimeStampMixinModel):
    """An address that must not be written to again, by hash.

    Kept until removed: a protective record, holding only the hash and the
    masked form. ``scope`` says how much it blocks.
    """

    class Reason(models.TextChoices):
        HARD_BOUNCE = "hard_bounce", _("Hard bounce")
        COMPLAINT = "complaint", _("Spam complaint")
        PROVIDER = "provider", _("Suppressed by the provider")
        MANUAL = "manual", _("Added manually")

    class Scope(models.TextChoices):
        ALL = "all", _("All email")
        MARKETING = "marketing", _("Marketing email")

    address_hash = models.CharField(
        _("Address hash"), max_length=64, unique=True
    )
    recipient_masked = models.CharField(_("Address"), max_length=120)
    reason = models.CharField(
        _("Reason"), max_length=16, choices=Reason.choices
    )
    scope = models.CharField(_("Scope"), max_length=16, choices=Scope.choices)
    source_log = models.ForeignKey(
        EmailLog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Source log"),
    )
    created_by_staff_id = models.BigIntegerField(
        _("Added by (staff)"),
        null=True,
        blank=True,
        help_text=_(
            "Id of the platform staff member who added it by hand (public "
            "schema, not a store user). Read it through "
            "``created_by_staff``."
        ),
    )
    created_by_staff = StaffActor()

    class Meta(TypedModelMeta):
        verbose_name = _("Email suppression")
        verbose_name_plural = _("Email suppressions")
        indexes = [*TimeStampMixinModel.Meta.indexes]

    def __str__(self) -> str:
        return self.recipient_masked
