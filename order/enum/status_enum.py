from enum import Enum
from enum import unique


@unique
class OrderStatusEnum(Enum):
    SENT = "Sent"
    PAID_AND_SENT = "Paid and sent"
    CANCELED = "Canceled"
    PENDING = "Pending"

    @classmethod
    def choices(cls) -> list:
        return [(name.value, name.name) for name in cls]
