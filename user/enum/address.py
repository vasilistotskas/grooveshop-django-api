from django.db import models
from django.utils.translation import gettext_lazy as _


class FloorChoicesEnum(models.IntegerChoices):
    BASEMENT = 0, _("Basement")
    GROUND_FLOOR = 1, _("Ground Floor")
    FIRST_FLOOR = 2, _("First Floor")
    SECOND_FLOOR = 3, _("Second Floor")
    THIRD_FLOOR = 4, _("Third Floor")
    FOURTH_FLOOR = 5, _("Fourth Floor")
    FIFTH_FLOOR = 6, _("Fifth Floor")
    SIXTH_FLOOR_PLUS = 7, _("Sixth Floor Plus")


class LocationChoicesEnum(models.IntegerChoices):
    HOME = 0, _("Home")
    OFFICE = 1, _("Office")
    OTHER = 2, _("Other")
