from enum import Enum
from enum import unique


@unique
class FloorChoicesEnum(Enum):
    BASEMENT = 0
    GROUND_FLOOR = 1
    FIRST_FLOOR = 2
    SECOND_FLOOR = 3
    THIRD_FLOOR = 4
    FOURTH_FLOOR = 5
    FIFTH_FLOOR = 6
    SIXTH_FLOOR_PLUS = 7

    @classmethod
    def choices(cls) -> list:
        return [(name.value, name.name) for name in cls]


@unique
class LocationChoicesEnum(Enum):
    HOME = 0
    OFFICE = 1
    OTHER = 2

    @classmethod
    def choices(cls) -> list:
        return [(name.value, name.name) for name in cls]
