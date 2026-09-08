from django.db import models
from django.utils.translation import gettext_lazy as _


class PaySettlement(models.TextChoices):
    """How the money for an order actually changes hands.

    This is the single discriminator the shipping layer needs, and it
    exists because the previous one — ``PayWay.is_cash_on_delivery``,
    derived from ``is_online_payment`` and ``requires_confirmation`` —
    conflated two genuinely different products and shipped two bugs:

    * 2026-05-01 (``891d5663`` / ``8fb8dcbc``): BoxNow's PAY ON THE GO
      was enabled by removing a filter that excluded *all* offline
      pay-ways. Correct in intent, but it left the generic courier COD
      pay-way selectable on lockers.
    * The consequence: a shopper choosing "Αντικαταβολή (+1,99 €)" with
      a locker was minted as BoxNow COD **and** charged a cash-handling
      surcharge, then paid by card at a terminal that accepts no cash
      and involves no courier.

    ``COURIER_CASH`` and ``CARRIER_TERMINAL`` are both "collected on
    delivery", which is why one boolean could not tell them apart. They
    are not interchangeable: only a courier can take cash at a door,
    and only a locker terminal can take a card at an APM.
    """

    ONLINE = "online", _("Paid online at checkout")
    COURIER_CASH = "courier_cash", _("Cash or card to the courier on delivery")
    CARRIER_TERMINAL = (
        "carrier_terminal",
        _("Card at the carrier's terminal on pickup"),
    )
    OFFLINE_TRANSFER = (
        "offline_transfer",
        _("Settled off-platform (e.g. bank transfer)"),
    )

    @classmethod
    def collected_on_delivery(cls) -> frozenset[PaySettlement]:
        """Settlements where the carrier collects money from the shopper.

        These are the ones that need a non-zero amount on the voucher.
        """
        return frozenset({cls.COURIER_CASH, cls.CARRIER_TERMINAL})
