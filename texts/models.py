from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta
from simple_history.models import HistoricalRecords

from core.models import TimeStampMixinModel
from tenant.staff_actors import (
    StaffActor,
    history_user_getter,
    history_user_setter,
)


class TextSurface(models.TextChoices):
    """Where an overridden text is shown. Emails join later."""

    STOREFRONT = "storefront", _("Storefront")


class TextOverride(TimeStampMixinModel):
    """One text a store replaced, in one language.

    Sparse: a text the store never edited has no row, and the platform's
    default (the storefront's locale files) applies. The catalogue of
    what can be edited lives in the storefront, so ``namespace`` and
    ``key`` are the storefront's own ids (``components/Chrome/Footer``,
    ``links.privacy``) and nothing here is a foreign key.

    Rows are written only through ``texts.services``, which validates
    against the storefront first; the admin has no add/change form.
    """

    surface = models.CharField(
        _("Surface"),
        max_length=32,
        choices=TextSurface.choices,
        default=TextSurface.STOREFRONT,
        db_default=TextSurface.STOREFRONT,
    )
    namespace = models.CharField(_("Namespace"), max_length=255)
    key = models.CharField(_("Key"), max_length=255)
    language = models.CharField(_("Language"), max_length=8)
    value = models.TextField(_("Text"))
    updated_by_staff_id = models.BigIntegerField(
        _("Updated by (staff)"),
        null=True,
        blank=True,
        help_text=_(
            "Id of the platform staff member who last saved this text "
            "(public schema, not a store user). Read it through "
            "``updated_by_staff``."
        ),
    )
    updated_by_staff = StaffActor()

    history = HistoricalRecords(
        history_user_id_field=models.BigIntegerField(null=True),
        history_user_getter=history_user_getter,
        history_user_setter=history_user_setter,
    )

    class Meta(TypedModelMeta):
        verbose_name = _("Text override")
        verbose_name_plural = _("Text overrides")
        ordering = ["namespace", "key", "language"]
        constraints = [
            models.UniqueConstraint(
                fields=["surface", "namespace", "key", "language"],
                name="texts_override_unique_text",
            ),
            models.CheckConstraint(
                condition=~Q(value=""),
                name="texts_override_value_not_empty",
            ),
        ]
        indexes = [
            *TimeStampMixinModel.Meta.indexes,
            models.Index(
                fields=["surface", "language"],
                name="texts_override_lang_ix",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.namespace}:{self.key} [{self.language}]"
