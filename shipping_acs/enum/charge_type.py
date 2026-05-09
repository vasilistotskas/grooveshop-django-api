from django.db import models
from django.utils.translation import gettext_lazy as _


class AcsChargeType(models.IntegerChoices):
    """ACS_Create_Voucher Charge_Type values.

    Per the ACS REST API PDF section "ΔΗΜΙΟΥΡΓΙΑ VOUCHER":

    * 1 — billing/charge against the partner (prepaid)
    * 2 — charge the recipient at delivery (cash on delivery)
    """

    PREPAID = 1, _("Prepaid")
    COD = 2, _("Cash on delivery")
