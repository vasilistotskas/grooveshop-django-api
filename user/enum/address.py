from django.db import models


class FloorChoicesEnum(models.IntegerChoices):
    BASEMENT = 0
    GROUND_FLOOR = 1
    FIRST_FLOOR = 2
    SECOND_FLOOR = 3
    THIRD_FLOOR = 4
    FOURTH_FLOOR = 5
    FIFTH_FLOOR = 6
    SIXTH_FLOOR_PLUS = 7


class LocationChoicesEnum(models.IntegerChoices):
    HOME = 0
    OFFICE = 1
    OTHER = 2
