from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from simple_history.models import HistoricalRecords

from core.models import TimeStampMixinModel
from emails.registry import UnknownEmailType, registry
from emails.types import KEY_MAX_LENGTH
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
