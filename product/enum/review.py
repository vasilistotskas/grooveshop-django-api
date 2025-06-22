from django.db import models
from django.utils.translation import gettext_lazy as _


class ReviewStatus(models.TextChoices):
    NEW = "NEW", _("New")
    TRUE = "TRUE", _("True")
    FALSE = "FALSE", _("False")


class RateEnum(models.IntegerChoices):
    ONE = 1, _("One")
    TWO = 2, _("Two")
    THREE = 3, _("Three")
    FOUR = 4, _("Four")
    FIVE = 5, _("Five")
    SIX = 6, _("Six")
    SEVEN = 7, _("Seven")
    EIGHT = 8, _("Eight")
    NINE = 9, _("Nine")
    TEN = 10, _("Ten")
