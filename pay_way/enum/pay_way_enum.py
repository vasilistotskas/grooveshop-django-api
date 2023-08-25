from enum import Enum
from enum import unique


@unique
class PayWayEnum(Enum):
    CREDIT_CARD = "Credit Card"
    PAY_ON_DELIVERY = "Pay On Delivery"
    PAY_ON_STORE = "Pay On Store"
    PAY_PAL = "PayPal"

    @classmethod
    def choices(cls) -> list:
        return [(name.value, name.name) for name in cls]
