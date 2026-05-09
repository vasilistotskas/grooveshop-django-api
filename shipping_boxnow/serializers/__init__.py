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
    # locker
    "BoxNowLockerSerializer",
    "BoxNowLockerDetailSerializer",
    "BoxNowNearestLockerRequestSerializer",
    "BoxNowNearestLockerResponseSerializer",
    # parcel event
    "BoxNowParcelEventSerializer",
    # shipment
    "BoxNowShipmentSerializer",
    "BoxNowShipmentDetailSerializer",
    # webhook
    "BoxNowEventLocationSerializer",
    "BoxNowCustomerSerializer",
    "BoxNowWebhookDataSerializer",
    "BoxNowWebhookEnvelopeSerializer",
    "BoxNowWebhookResponseSerializer",
]
