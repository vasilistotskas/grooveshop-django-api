from django.db import models
from django.utils.translation import pgettext_lazy


# Context-qualified: "Paid" and "Social" already exist as msgids with
# other meanings ("Πληρώθηκε" for a settled payment), and a bare msgid
# is one translation for every call site.
class OrderSourceType(models.TextChoices):
    """What kind of channel brought the shopper who placed an order."""

    DIRECT = "direct", pgettext_lazy("order source type", "Direct")
    CAMPAIGN = "campaign", pgettext_lazy("order source type", "Campaign")
    PAID = "paid", pgettext_lazy("order source type", "Paid")
    SOCIAL = "social", pgettext_lazy("order source type", "Social")
    SEARCH = "search", pgettext_lazy("order source type", "Search")
    REFERRAL = "referral", pgettext_lazy("order source type", "Referral")
    AGENT = "agent", pgettext_lazy("order source type", "AI agent")
