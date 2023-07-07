from enum import Enum
from enum import unique


@unique
class TipKindEnum(Enum):
    SUCCESS = "success"
    INFO = "info"
    ERROR = "error"
    WARNING = "warning"

    @classmethod
    def choices(cls) -> list:
        return [(name.value, name.name) for name in cls]
