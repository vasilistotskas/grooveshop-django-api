from __future__ import annotations

from datetime import datetime, timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta

#: The hold that is in force: the one row that has not ended.
IN_FORCE = Q(ended_at__isnull=True)


class AcsVoucherHold(models.Model):
    """A span during which the store sends no voucher request to ACS.

    It begins when an ``ACS_Create_Voucher`` this store sent ends with an
    unknown outcome (``AcsOutcomeUnknownError`` and the other cases of
    ``AcsService._voucher_request_lost``): ACS is failing in a way that
    leaves every further request as one more order for staff to check at
    the ACS portal, so the requests wait instead
    (``shipping_acs.voucher_hold``). At most one span is in force
    (``ended_at`` empty) at a time, enforced by the database. The table is
    in the store's schema, so one store's hold never holds another's.

    While in force the hold is in one of two states:

    * **closed** (``trial_allowed_at`` empty): nothing is sent.
    * **one request first** (``trial_allowed_at`` set, by a read ACS
      answered or by staff): one voucher request at a time is sent, each
      for ``trial_until``. An answer from ACS ends the hold; an unknown
      outcome closes it again and doubles the wait.
    """

    started_at = models.DateTimeField(_("Held since"))
    ended_at = models.DateTimeField(_("Ended at"), null=True, blank=True)
    reason = models.TextField(
        _("ACS failure"),
        blank=True,
        default="",
        help_text=_(
            "The last voucher request whose answer was lost, as ACS or the "
            "network reported it."
        ),
    )
    last_failure_at = models.DateTimeField(_("Last lost answer at"))
    failed_trials = models.PositiveSmallIntegerField(
        _("Failed first requests"),
        default=0,
        help_text=_(
            "How many times the one request sent first also lost its "
            "answer. Each one doubles the wait before ACS is asked again."
        ),
    )
    trial_allowed_at = models.DateTimeField(
        _("One request allowed since"),
        null=True,
        blank=True,
        help_text=_(
            "Set when ACS answered a read, or staff said it answers again. "
            "One voucher request is then sent first; the rest follow only "
            "after ACS answers it."
        ),
    )
    trial_until = models.DateTimeField(
        _("First request in flight until"), null=True, blank=True
    )

    class Meta(TypedModelMeta):
        verbose_name = _("ACS voucher hold")
        verbose_name_plural = _("ACS voucher holds")
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["ended_at"],
                condition=IN_FORCE,
                nulls_distinct=False,
                name="acs_voucher_hold_one_in_force",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.started_at:%Y-%m-%d %H:%M}"

    @property
    def retry_after(self) -> datetime:
        """When ACS may next be asked whether it answers again.

        ``ACS_VOUCHER_HOLD_RETRY_SECONDS`` after the last lost answer,
        doubled for every first request that failed too, up to
        ``ACS_VOUCHER_HOLD_RETRY_MAX_SECONDS``.
        """
        wait = min(
            settings.ACS_VOUCHER_HOLD_RETRY_SECONDS * 2**self.failed_trials,
            settings.ACS_VOUCHER_HOLD_RETRY_MAX_SECONDS,
        )
        return self.last_failure_at + timedelta(seconds=wait)

    def holds(self, now: datetime) -> bool:
        """Whether a voucher request arriving at ``now`` must wait: the
        hold is closed, or its one request is still in flight."""
        return self.trial_allowed_at is None or (
            self.trial_until is not None and self.trial_until > now
        )
