from __future__ import annotations

from django.db import models
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _
from django_stubs_ext.db.models import TypedModelMeta


class RefundNotification(models.Model):
    """The claim on the confirmation email of one refund.

    A refund's email is sent once, by whichever task claims it first: the
    claim is the insert of this row, and ``refund`` being a one-to-one makes
    the database the arbiter, so two workers handed the same refund (a
    task delivered twice, a retry racing its original) cannot both send.
    The row is deleted again when the send ultimately fails, so a later
    attempt can claim the refund anew.

    A side table rather than a column on ``PaymentTransaction``: the
    ledger's rows are the money facts and are never updated, and who has
    been told about a refund is bookkeeping with a lifecycle of its own.
    """

    id = models.BigAutoField(primary_key=True)
    refund = models.OneToOneField(
        "order.PaymentTransaction",
        related_name="notification",
        # Follows the ledger row: the claim means nothing without it, and
        # it lets a whole order's ledger be removed in one statement.
        on_delete=models.CASCADE,
        verbose_name=_("Refund"),
        help_text=_("The refund whose confirmation email this claims."),
    )
    created_at = models.DateTimeField(_("Created At"), auto_now_add=True)

    class Meta(TypedModelMeta):
        verbose_name = _("Refund Notification")
        verbose_name_plural = _("Refund Notifications")

    def __str__(self) -> str:
        return gettext("Notification of refund %(refund)s") % {
            "refund": self.refund_id,
        }
