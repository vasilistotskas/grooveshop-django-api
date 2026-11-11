from django.db import models
from django.utils.translation import gettext_lazy as _


class AcsCodPaymentWay(models.IntegerChoices):
    """ACS_Create_Voucher Cod_Payment_Way values.

    Per the ACS REST API PDF (when ``Cod_Ammount`` is set):

    * 0 — cash collection
    * 1 — credit-card terminal at recipient's door
    """

    CASH = 0, _("Cash")
    CARD = 1, _("Card")
