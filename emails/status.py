"""What became of one recipient's send.

One enum for ``send_email``'s return value (``Delivery.status``) and the
``EmailLog.status`` column, so the two cannot drift. It lives in its own
module because ``send`` imports ``models`` and ``models`` needs the choices.
"""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class Status(models.TextChoices):
    #: The row exists and the transport has not answered yet. A row that
    #: stays here means the process died between the two.
    QUEUED = "queued", _("Queued")
    SENT = "sent", _("Sent")
    FAILED = "failed", _("Failed")
    SKIPPED_FEATURE_OFF = "skipped_feature_off", _("Skipped: feature off")
    SKIPPED_SWITCHED_OFF = "skipped_switched_off", _("Skipped: switched off")
    SKIPPED_NO_RECIPIENT = "skipped_no_recipient", _("Skipped: no recipient")
    SKIPPED_OPTED_OUT = "skipped_opted_out", _("Skipped: opted out")
