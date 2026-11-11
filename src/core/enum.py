from django.db import models
from django.utils.translation import gettext_lazy as _


class FloorChoicesEnum(models.TextChoices):
    BASEMENT = "BASEMENT", _("Basement")
    GROUND_FLOOR = "GROUND_FLOOR", _("Ground Floor")
    FIRST_FLOOR = "FIRST_FLOOR", _("First Floor")
    SECOND_FLOOR = "SECOND_FLOOR", _("Second Floor")
    THIRD_FLOOR = "THIRD_FLOOR", _("Third Floor")
    FOURTH_FLOOR = "FOURTH_FLOOR", _("Fourth Floor")
    FIFTH_FLOOR = "FIFTH_FLOOR", _("Fifth Floor")
    SIXTH_FLOOR_PLUS = "SIXTH_FLOOR_PLUS", _("Sixth Floor Plus")


class LocationChoicesEnum(models.TextChoices):
    HOME = "HOME", _("Home")
    OFFICE = "OFFICE", _("Office")
    OTHER = "OTHER", _("Other")
