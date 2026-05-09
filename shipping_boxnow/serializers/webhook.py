from django.utils.translation import gettext_lazy as _
from rest_framework import serializers


class BoxNowEventLocationSerializer(serializers.Serializer):
    """
    Nested ``eventLocation`` object within a BoxNow webhook payload.
    """

    displayName = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_("Human-readable locker or hub name"),
    )
    postalCode = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_("Postal code of the event location"),
    )


class BoxNowCustomerSerializer(serializers.Serializer):
    """
    Nested ``customer`` object within a BoxNow webhook payload.
    """

    name = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_("Customer full name"),
    )
    email = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_("Customer email address"),
    )
    phoneNumber = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_("Customer phone number"),
    )


class BoxNowWebhookDataSerializer(serializers.Serializer):
    """
    ``data`` field of a BoxNow CloudEvent webhook envelope.

    Reflects the payload shape described in the BoxNow webhook PDF.
    ``camelCase`` field names are preserved because
    ``djangorestframework-camel-case`` is already applied
    project-wide at the middleware level; no manual aliasing needed.
    """

    parcelId = serializers.CharField(
        help_text=_("10-digit BoxNow voucher/parcel number"),
    )
    parcelState = serializers.CharField(
        help_text=_("BoxNow parcel state vocabulary (data.parcelState)"),
    )
    parcelReferenceNumber = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_("Optional merchant reference number"),
    )
    parcelName = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_("Optional descriptive parcel name"),
    )
    orderNumber = serializers.CharField(
        help_text=_(
            "Merchant order number sent during delivery-request creation"
        ),
    )
    event = serializers.CharField(
        help_text=_(
            "BoxNow event type string (e.g. 'in-depot', "
            "'final-destination', 'delivered')"
        ),
    )
    eventLocation = BoxNowEventLocationSerializer(
        required=False,
        help_text=_("Location context for this event"),
    )
    customer = BoxNowCustomerSerializer(
        required=False,
        help_text=_("Customer details associated with the parcel"),
    )
    additionalInformation = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text=_("Free-text additional information from BoxNow"),
    )
    time = serializers.DateTimeField(
        help_text=_("Timestamp when the event occurred at BoxNow"),
    )


class BoxNowWebhookEnvelopeSerializer(serializers.Serializer):
    """
    Top-level CloudEvent envelope for BoxNow webhook POST requests.

    Used for OpenAPI documentation only — the webhook view reads
    raw bytes before parsing in order to verify the HMAC-SHA256
    ``datasignature`` against the unmodified ``data`` JSON substring.
    DRF validation is not applied to the incoming request directly.
    """

    specversion = serializers.CharField(
        help_text=_("CloudEvents specification version (expected: '1.0')"),
    )
    type = serializers.CharField(
        help_text=_("Event type (expected: 'gr.boxnow.parcel_event_change')"),
    )
    source = serializers.URLField(
        help_text=_("Origin URL of the event source"),
    )
    subject = serializers.CharField(
        help_text=_("Subject of the event (typically the parcel ID)"),
    )
    id = serializers.CharField(
        help_text=_(
            "Unique CloudEvents ID; used as idempotency key "
            "(stored as webhook_message_id)"
        ),
    )
    time = serializers.DateTimeField(
        help_text=_("Timestamp the envelope was generated"),
    )
    datacontenttype = serializers.CharField(
        help_text=_(
            "MIME type of the data field (expected: 'application/json')"
        ),
    )
    datasignature = serializers.CharField(
        help_text=_(
            "HMAC-SHA256 hex digest of the raw 'data' JSON object; "
            "used for signature verification"
        ),
    )
    data = BoxNowWebhookDataSerializer(
        help_text=_("Parcel event payload"),
    )


class BoxNowWebhookResponseSerializer(serializers.Serializer):
    """
    Empty 200 OK response returned to BoxNow after successful processing.

    BoxNow expects an HTTP 200 response body to be empty JSON ``{}``.
    Returning 200 (including for already-processed duplicate messages)
    prevents BoxNow's retry logic from re-delivering the same event.
    """
