from django.db import models
from django.utils.translation import gettext_lazy as _


class AcsShopKind(models.IntegerChoices):
    """ACS_SHOP_KIND values returned by the ``Acs_Stations`` endpoint.

    Per the ACS REST API PDF p.28 (section "ΣΤΑΘΜΟΙ ACS"):

    Greece:
    * 1 — central ACS shops
    * 2 / 3 — sub-shops (υποκαταστήματα)
    * 4 — ACS Xpress points (cash pickups only)
    * 5 — ACS Kiosks (standardized envelopes only)
    * 7 — ACS Smartpoints WITHOUT a locker — staffed points for
      standardized envelopes/parcels up to 6 kg ("δίχως locker")
    * 8 — ACS Smartpoints WITH a locker, up to 6 kg ("με locker")

    Cyprus:
    * 1 — central shops
    * 2 / 3 — return nothing
    * 4 — ACS Shop in a Shop
    * 5 — returns nothing
    * 7 — returns nothing (per the PDF; CY has no Smartpoint tier)

    For Phase 2 locker pickup we filter on kinds 7 and 8. Kind 7 is
    currently empty upstream for GR (verified against the live API
    2026-07-25: 0 rows, vs 1,485 kind-8 lockers) but stays configured
    so an ACS re-launch of the no-locker Smartpoint tier is picked up
    automatically by the daily sync.
    """

    SHOP = 1, _("Shop")
    PARTNER_SHOP_2 = 2, _("Partner shop (2)")
    PARTNER_SHOP_3 = 3, _("Partner shop (3)")
    XPRESS_POINT = 4, _("Xpress Point")
    KIOSK = 5, _("Kiosk")
    SMARTPOINT = 7, _("Smartpoint (no locker)")
    SMARTPOINT_LOCKER = 8, _("Smartpoint locker")
