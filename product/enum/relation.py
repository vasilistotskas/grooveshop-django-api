from django.db import models
from django.utils.translation import gettext_lazy as _


class RelationType(models.TextChoices):
    """What a merchant means when they link two products.

    A single "related products" list is too weak for a multi-vertical
    platform: to a fashion store "related" is *the same look in another
    colour*, to a nursery it is *the pot and soil for this plant*, to a
    phone shop it is *a compatible accessory*. None of that is
    inferable from a catalogue, so the type is stated by the merchant
    and the recommendation engine reads it — it decides which surface
    a relation belongs on (a ``REPLACEMENT`` matters on an
    out-of-stock page, a ``COMPLEMENTARY`` in the cart) and how the
    storefront labels it.

    Relations are DIRECTIONAL. "Accessory of" is not symmetric: the
    case belongs on the phone's page, the phone does not belong on the
    case's. A merchant who wants both directions creates both rows.

    Values are lower_snake like ``PaySettlement``; the wire value is
    the API contract, the label is display only.
    """

    SIMILAR = "similar", _("Similar product")
    COMPLEMENTARY = "complementary", _("Goes well with")
    ACCESSORY = "accessory", _("Accessory for")
    REPLACEMENT = "replacement", _("Replacement for")
    BUNDLE = "bundle", _("Bundle with")
