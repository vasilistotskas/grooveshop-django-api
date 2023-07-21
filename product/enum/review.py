from enum import Enum
from enum import unique


@unique
class StatusEnum(Enum):
    NEW = "New"
    TRUE = "True"
    FALSE = "False"

    @classmethod
    def choices(cls) -> list:
        return [(name.value, name.name) for name in cls]


@unique
class RateEnum(Enum):
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

    @classmethod
    def choices(cls) -> list:
        return [(name.value, name.name) for name in cls]
