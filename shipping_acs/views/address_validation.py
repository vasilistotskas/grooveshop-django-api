"""Phase 4b: ACS address validation proxy endpoint.

Wraps :meth:`shipping_acs.client.AcsClient.address_validation` so the
checkout UI can debounce-validate the shopper's street address as
they type, surfacing the closest geocoded match and (when available)
the serving ACS station code.

Public — anyone can submit an address for validation; the endpoint
never echoes credentials or sensitive data back, and the underlying
ACS API is rate-limited at 10 req/sec server-side.  We still cache
validations in Redis for 1 hour per identical input to absorb the
typical typing-storm pattern (street → zip → typo → fix).
"""

from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.core.cache import cache
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.throttling import AcsAddressValidationThrottle
from shipping_acs.exceptions import AcsAPIError, AcsConfigError

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


class AcsAddressValidationRequestSerializer(serializers.Serializer):
    """Free-text address payload — ACS does its own parsing."""

    address = serializers.CharField(
        max_length=500,
        help_text="Street + number + optional zip + city, e.g. 'Pireos 25 17778'.",
    )
    address_id = serializers.CharField(
        max_length=64,
        required=False,
        allow_blank=True,
        help_text="Optional re-validation key from a previous response.",
    )
    language = serializers.CharField(
        max_length=2,
        required=False,
        allow_blank=True,
        default="GR",
    )


class AcsAddressValidationResponseSerializer(serializers.Serializer):
    """A single resolved address — the first ACSObjectOutput row."""

    geo_id = serializers.IntegerField(allow_null=True, required=False)
    resolved_street = serializers.CharField(allow_blank=True)
    resolved_street_num = serializers.CharField(allow_blank=True)
    resolved_zip = serializers.CharField(allow_blank=True)
    resolved_area = serializers.CharField(allow_blank=True)
    resolved_long = serializers.FloatField(allow_null=True, required=False)
    resolved_lat = serializers.FloatField(allow_null=True, required=False)
    resolved_station_id = serializers.CharField(allow_blank=True)
    resolved_branch_id = serializers.IntegerField(
        allow_null=True, required=False
    )
    resolved_providence = serializers.CharField(allow_blank=True)
    address_id = serializers.CharField(allow_blank=True)


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------


class AcsAddressValidationView(APIView):
    """POST /api/v1/shipping/acs/address-validation."""

    permission_classes = [AllowAny]
    # Per-IP scoped throttle on top of the global anon cap — this public
    # proxy forwards to the rate-limited ACS partner API, and the Redis cache
    # only absorbs identical inputs (G0016).
    throttle_classes = [AcsAddressValidationThrottle]
    serializer_class = AcsAddressValidationRequestSerializer

    @extend_schema(
        operation_id="validateAcsAddress",
        summary="Validate / geocode a Greek address against ACS's catalogue",
        description=(
            "Calls ACS_Address_Validation with the supplied street + "
            "zip + city string. Returns the first resolved match. "
            "Empty 200 body when ACS could not geocode — frontends "
            "should treat that as 'address not recognised, fall back "
            "to free-text submission'."
        ),
        request=AcsAddressValidationRequestSerializer,
        responses={
            200: AcsAddressValidationResponseSerializer,
            400: None,
            502: None,
        },
        tags=["ACS shipments"],
    )
    def post(self, request: Request) -> Response:
        from shipping_acs.client import AcsClient

        serializer = AcsAddressValidationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        address = serializer.validated_data["address"].strip()
        address_id = serializer.validated_data.get("address_id") or None
        language = serializer.validated_data.get("language") or "GR"

        # The address is shopper-typed free text (spaces, Greek, anything) —
        # hash it so the cache key stays within Django's memcached-safe
        # charset instead of tripping CacheKeyWarning on every lookup.
        address_digest = hashlib.sha256(address.encode("utf-8")).hexdigest()
        cache_key = f"acs:address_validation:{language}:{address_digest}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(_normalise_response(cached))

        try:
            client = AcsClient()
            raw = client.address_validation(
                address=address,
                address_id=address_id,
                language=language,
            )
        except AcsConfigError as exc:
            logger.warning("ACS address validation unavailable: %s", exc)
            return Response(
                {"detail": "ACS address validation is not configured."},
                status=503,
            )
        except AcsAPIError as exc:
            logger.warning("ACS address validation failed: %s", exc)
            return Response(
                {"detail": str(exc)},
                status=502,
            )

        cache.set(
            cache_key,
            raw,
            timeout=getattr(settings, "ACS_ADDRESS_VALIDATION_CACHE_TTL", 3600),
        )
        return Response(_normalise_response(raw))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_response(raw: dict) -> dict:
    """Lower-case keys and surface only the fields the UI cares about.

    ACS returns mixed-case keys with leading capitals (``GeoID``,
    ``Resolved_Street`` etc.); the Nuxt frontend renders them via
    drf-camel-case so we use lower_snake here so the output matches
    the rest of our API.
    """
    if not raw:
        return {}
    return {
        "geo_id": raw.get("GeoID"),
        "resolved_street": raw.get("Resolved_Street") or "",
        "resolved_street_num": str(raw.get("Resolved_Street_Num") or ""),
        "resolved_zip": str(raw.get("Resolved_Zip") or ""),
        "resolved_area": raw.get("Resolved_Area") or "",
        "resolved_long": raw.get("Resolved_Long"),
        "resolved_lat": raw.get("Resolved_Lat"),
        "resolved_station_id": str(raw.get("Resolved_Station_ID") or ""),
        "resolved_branch_id": raw.get("Resolved_Branch_ID"),
        "resolved_providence": raw.get("Resolved_Providence") or "",
        "address_id": str(raw.get("AddressID") or ""),
    }
