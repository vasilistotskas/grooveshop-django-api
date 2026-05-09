from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from shipping_boxnow.models.locker import BoxNowLocker


class BoxNowLockerSerializer(serializers.ModelSerializer[BoxNowLocker]):
    """
    Lightweight serializer for BoxNow locker list endpoints.

    Lockers are populated exclusively via the ``sync_boxnow_lockers``
    Celery task — all fields are read-only.
    """

    class Meta:
        model = BoxNowLocker
        fields = (
            "id",
            "external_id",
            "type",
            "image_url",
            "lat",
            "lng",
            "title",
            "name",
            "address_line_1",
            "address_line_2",
            "postal_code",
            "country_code",
            "note",
            "is_active",
            "last_synced_at",
            "created_at",
            "updated_at",
            "uuid",
        )
        read_only_fields = fields


class BoxNowLockerDetailSerializer(BoxNowLockerSerializer):
    """
    Detail serializer for a single BoxNow locker.

    Inherits all fields from ``BoxNowLockerSerializer``.
    Extended fields can be added here as requirements grow.
    """

    class Meta(BoxNowLockerSerializer.Meta):
        pass


class BoxNowNearestLockerRequestSerializer(serializers.Serializer):
    """
    Request body for ``POST /lockers/nearest``.

    Maps to the BoxNow ``/api/v2/delivery-requests:checkAddressDelivery``
    endpoint parameters.
    """

    city = serializers.CharField(
        max_length=128,
        help_text=_("City name for nearest-locker lookup"),
    )
    street = serializers.CharField(
        max_length=255,
        help_text=_("Street name and number"),
    )
    postal_code = serializers.CharField(
        max_length=16,
        help_text=_("Postal / ZIP code"),
    )
    region = serializers.CharField(
        max_length=8,
        default="el-GR",
        required=False,
        help_text=_("IETF language tag / region code (default: el-GR)"),
    )
    compartment_size = serializers.IntegerField(
        min_value=1,
        max_value=3,
        default=1,
        required=False,
        help_text=_("Required compartment size: 1=Small, 2=Medium, 3=Large"),
    )


class BoxNowNearestLockerResponseSerializer(serializers.Serializer):
    """
    Response shape returned by BoxNow's checkAddressDelivery call.

    Mirrors ``/api/v2/delivery-requests:checkAddressDelivery`` response.
    ``lat`` and ``lng`` are CharField because BoxNow returns them as
    strings in this endpoint.  ``distance`` is the straight-line
    distance in kilometres from the supplied address.
    """

    id = serializers.CharField(
        read_only=True,
        help_text=_("BoxNow APM identifier"),
    )
    type = serializers.CharField(
        read_only=True,
        help_text=_("Locker type (e.g. apm, warehouse)"),
    )
    image = serializers.CharField(
        read_only=True,
        help_text=_("URL of locker image"),
    )
    lat = serializers.CharField(
        read_only=True,
        help_text=_("Latitude (string as returned by BoxNow)"),
    )
    lng = serializers.CharField(
        read_only=True,
        help_text=_("Longitude (string as returned by BoxNow)"),
    )
    title = serializers.CharField(
        read_only=True,
        help_text=_("Short display title"),
    )
    name = serializers.CharField(
        read_only=True,
        help_text=_("Full locker name"),
    )
    postal_code = serializers.CharField(
        read_only=True,
        help_text=_("Postal code of the locker"),
    )
    country = serializers.CharField(
        read_only=True,
        help_text=_("ISO 3166-1 alpha-2 country code"),
    )
    note = serializers.CharField(
        read_only=True,
        help_text=_("Operational note from BoxNow"),
    )
    address_line_1 = serializers.CharField(
        read_only=True,
        help_text=_("Primary address line"),
    )
    address_line_2 = serializers.CharField(
        read_only=True,
        help_text=_("Secondary address line"),
    )
    region = serializers.CharField(
        read_only=True,
        help_text=_("IETF region tag returned by BoxNow"),
    )
    distance = serializers.FloatField(
        read_only=True,
        help_text=_("Distance from the supplied address in kilometres"),
    )
