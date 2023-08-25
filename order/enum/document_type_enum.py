from enum import Enum
from enum import unique


@unique
class OrderDocumentTypeEnum(Enum):
    RECEIPT = "receipt"
    INVOICE = "invoice"

    @classmethod
    def choices(cls) -> list:
        return [(name.value, name.name) for name in cls]
