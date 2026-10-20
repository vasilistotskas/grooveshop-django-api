from typing import NamedTuple

from django.db import models
from django.utils.translation import gettext_lazy as _


class BoxNowCompartmentSize(models.IntegerChoices):
    SMALL = 1, _("Μικρό")
    MEDIUM = 2, _("Μεσαίο")
    LARGE = 3, _("Μεγάλο")


class BoxNowCompartmentLimits(NamedTuple):
    """Inner size of one locker compartment, in centimetres."""

    height: float
    width: float
    length: float


# BOX NOW publishes these sizes in its FAQ (https://boxnow.gr/en/faq,
# "What size of parcels can I receive or send from BOX NOW Lockers?"):
# Small H 8 x W 45 x L 60, Medium H 17 x W 45 x L 60, Large H 36 x W 45
# x L 60, each up to 20 kg (the weight cap lives on ``ShippingRate``).
# The FAQ also lists a Mini (H 8 x W 24 x L 60); the partner API's
# ``compartmentSize`` has no value for it, so a Mini-sized parcel is
# booked as Small.
COMPARTMENT_LIMITS_CM: dict[BoxNowCompartmentSize, BoxNowCompartmentLimits] = {
    BoxNowCompartmentSize.SMALL: BoxNowCompartmentLimits(8, 45, 60),
    BoxNowCompartmentSize.MEDIUM: BoxNowCompartmentLimits(17, 45, 60),
    BoxNowCompartmentSize.LARGE: BoxNowCompartmentLimits(36, 45, 60),
}
