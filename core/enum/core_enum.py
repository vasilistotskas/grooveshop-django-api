from enum import Enum
from enum import unique


@unique
class LangCodesEnum(Enum):
    EN = "en-US"
    GR = "gr-GR"

    @classmethod
    def choices(cls) -> list:
        return [(name.value, name.name) for name in cls]
