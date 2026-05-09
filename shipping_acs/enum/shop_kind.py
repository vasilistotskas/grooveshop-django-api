from django.db import models
from django.utils.translation import gettext_lazy as _


class AcsShopKind(models.IntegerChoices):
    """ACS_SHOP_KIND values returned by the ``Acs_Stations`` endpoint.

    Per the ACS REST API PDF section "ΣΤΑΘΜΟΙ ACS":

    Greece:
    * 1 — physical ACS shop
    * 2 / 3 — partner shops
    * 4 — ACS Xpress Point (full-service)
    * 5 — ACS Kiosk
    * 7 — ACS Smartpoint locker (deliveries up to 6 kg)
    * 8 — ACS Smartpoint locker (returns / outbound, up to 6 kg)

    Cyprus:
    * 1 — physical ACS shop
    * 2 / 3 — partner shops
    * 4 — Shop in a Shop
    * 5 — partner / kiosk
    * 7 — partner kiosk

    For Phase 2 locker pickup we filter on kinds 7 and 8 (GR Smartpoints)
    plus 7 (CY) when the customer's country supports it.
    """

    SHOP = 1, _("Shop")
    PARTNER_SHOP_2 = 2, _("Partner shop (2)")
    PARTNER_SHOP_3 = 3, _("Partner shop (3)")
    XPRESS_POINT = 4, _("Xpress Point")
    KIOSK = 5, _("Kiosk")
    SMARTPOINT_INBOUND = 7, _("Smartpoint (inbound)")
    SMARTPOINT_OUTBOUND = 8, _("Smartpoint (outbound)")
