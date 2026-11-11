from django.db import models
from django.utils.translation import pgettext_lazy


class LegalAcceptanceSource(models.TextChoices):
    """How the buyer accepted an order's legal documents."""

    # The shopper ticked the storefront's consent checkbox, which links
    # both documents, before placing the order.
    CHECKOUT_CONSENT = (
        "checkout_consent",
        pgettext_lazy("legal acceptance source", "Checkout consent"),
    )
    # An AI agent completed a UCP/ACP checkout whose response linked the
    # documents for it to present to the buyer. Proven by the gateway's
    # shared secret, as the agent attribution is.
    AGENT_CHECKOUT = (
        "agent_checkout",
        pgettext_lazy("legal acceptance source", "AI agent checkout"),
    )
