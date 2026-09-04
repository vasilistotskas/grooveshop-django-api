from shipping_boxnow.serializers.locker import (
    BoxNowLockerDetailSerializer,
    BoxNowLockerSerializer,
    BoxNowNearestLockerRequestSerializer,
    BoxNowNearestLockerResponseSerializer,
)
from shipping_boxnow.serializers.parcel_event import (
    BoxNowParcelEventSerializer,
)
from shipping_boxnow.serializers.shipment import (
    BoxNowShipmentDetailSerializer,
    BoxNowShipmentSerializer,
)
from shipping_boxnow.serializers.webhook import (
    BoxNowCustomerSerializer,
    BoxNowEventLocationSerializer,
    BoxNowWebhookDataSerializer,
    BoxNowWebhookEnvelopeSerializer,
    BoxNowWebhookResponseSerializer,
)

__all__ = [
    "BoxNowCustomerSerializer",
    # webhook
    "BoxNowEventLocationSerializer",
    "BoxNowLockerDetailSerializer",
    # locker
    "BoxNowLockerSerializer",
    "BoxNowNearestLockerRequestSerializer",
    "BoxNowNearestLockerResponseSerializer",
    # parcel event
    "BoxNowParcelEventSerializer",
    "BoxNowShipmentDetailSerializer",
    # shipment
    "BoxNowShipmentSerializer",
    "BoxNowWebhookDataSerializer",
    "BoxNowWebhookEnvelopeSerializer",
    "BoxNowWebhookResponseSerializer",
]
