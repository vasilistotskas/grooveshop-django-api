from django.db import models
from django.utils.translation import gettext_lazy as _


class ReviewStatusEnum(models.TextChoices):
    NEW = "NEW", _("New")
    TRUE = "TRUE", _("True")
    FALSE = "FALSE", _("False")


class RateEnum(models.IntegerChoices):
    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6
    SEVEN = 7
    EIGHT = 8
    NINE = 9
    TEN = 10
