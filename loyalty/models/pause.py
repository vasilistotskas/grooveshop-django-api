from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from core.models import FeaturePauseModel


class LoyaltyPause(FeaturePauseModel):
    """A span during which the store had its loyalty programme switched
    off. Points do not age during it: their expiry counts only the time
    the programme was on."""

    class Meta(FeaturePauseModel.Meta):
        verbose_name = _("Loyalty Pause")
        verbose_name_plural = _("Loyalty Pauses")
        db_table = "loyalty_pause"

    def __str__(self):
        return gettext("Loyalty off from %(start)s") % {
            "start": self.started_at
        }
