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
      surcharge for a courier who was never involved.

    ``COURIER_CASH`` and ``CARRIER_TERMINAL`` are both "collected on
    delivery", which is why one boolean could not tell them apart. They
    are not interchangeable: a courier takes cash or card at the door,
    while the carrier collects for an APM parcel without anyone
    meeting the shopper.

    **What CARRIER_TERMINAL actually is.** Read from BoxNow's own pages
    on 2026-09-09 (boxnow.gr/antikatavoli/odigies), because the code
    used to describe it as a card reader on the locker and that is
    wrong — it misled a later change. BOX NOW Αντικαταβολή (marketed in
    English as PAY ON THE GO) is an ONLINE payment the carrier
    collects: BoxNow emails a Viva Wallet link when it picks the parcel
    up, sends a Viber/SMS with an INACTIVE pickup PIN plus the link
    once the parcel is in the compartment, and the PIN activates
    automatically when the shopper pays — by card, Apple Pay, Google
    Pay, IRIS or bank transfer, from wherever they happen to be. There
    is no terminal and no cash.

    The name and the wire value stay ``carrier_terminal`` regardless:
    what the enum discriminates is WHO collects and WHEN — the carrier,
    after dispatch — and that is unchanged. Renaming the value would be
    a data migration for no behavioural gain.
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
