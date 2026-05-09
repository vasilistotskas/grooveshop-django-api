from django.db import models
from django.utils.translation import gettext_lazy as _


class BoxNowCompartmentSize(models.IntegerChoices):
    SMALL = 1, _("Μικρό")
    MEDIUM = 2, _("Μεσαίο")
    LARGE = 3, _("Μεγάλο")
