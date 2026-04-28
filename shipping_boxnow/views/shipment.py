"""BoxNow shipment label download and cancellation views.

Both views operate on ``BoxNowShipment`` objects looked up by
``parcel_id`` (the 10-digit BoxNow voucher number).

* ``BoxNowLabelView``  — streams the label PDF to the caller.
* ``BoxNowCancelView`` — admin-only cancellation via the BoxNow API.
"""

from __future__ import annotations

import io
import logging

from django.http import FileResponse
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.permissions import IsOwnerOrAdminOrGuest
from shipping_boxnow.models import BoxNowShipment
from shipping_boxnow.serializers import BoxNowShipmentSerializer

logger = logging.getLogger(__name__)


class BoxNowLabelView(APIView):
    """Stream the BoxNow parcel label PDF.

    Permission model mirrors the order-detail rules:
    authenticated owner, staff, or guest with a matching UUID query
    parameter (``IsOwnerOrAdminOrGuest`` is evaluated against the
    related Order object).
    """

    permission_classes = [IsOwnerOrAdminOrGuest]
    serializer_class = BoxNowShipmentSerializer

    @extend_schema(
        operation_id="getBoxNowLabel",
        summary="Download BoxNow parcel label PDF",
        description=(
            "Fetches the BoxNow label PDF for the given parcel ID and"
            " streams it as an attachment.  Accessible by the order"
            " owner, staff, or a guest with the order UUID."
        ),
        tags=["BoxNow shipments"],
        responses={
            200: bytes,
            403: None,
            404: None,
        },
    )
    def get(self, request: Request, parcel_id: str) -> FileResponse:
        """Return the label PDF for ``parcel_id``."""
        from shipping_boxnow.services import BoxNowService

        try:
            shipment = BoxNowShipment.objects.select_related("order").get(
                parcel_id=parcel_id
            )
        except BoxNowShipment.DoesNotExist:
            from rest_framework.exceptions import NotFound

            raise NotFound(f"No shipment found with parcel ID {parcel_id!r}.")

        # Enforce IsOwnerOrAdminOrGuest against the Order.
        self.check_object_permissions(request, shipment.order)

        label_bytes: bytes = BoxNowService.fetch_label_bytes(shipment)
        return FileResponse(
            io.BytesIO(label_bytes),
            content_type="application/pdf",
            as_attachment=True,
            filename=f"boxnow_{parcel_id}.pdf",
        )


class BoxNowCancelView(APIView):
    """Cancel a BoxNow parcel (admin-only).

    Calls the BoxNow cancellation API and marks
    ``BoxNowShipment.cancel_requested_at``.  Only permitted when the
    parcel is in ``NEW`` state per BoxNow docs.
    """

    permission_classes = [IsAdminUser]
    serializer_class = BoxNowShipmentSerializer

    @extend_schema(
        operation_id="cancelBoxNowShipment",
        summary="Cancel a BoxNow parcel",
        description=(
            "Admin-only.  Requests parcel cancellation via the BoxNow"
            " API.  Only valid when parcel state is NEW (BoxNow error"
            " P420 otherwise).  Accepts an optional ``reason`` field in"
            " the request body."
        ),
        tags=["BoxNow shipments"],
        responses={
            200: None,
            400: None,
            403: None,
            404: None,
        },
    )
    def post(self, request: Request, parcel_id: str) -> Response:
        """Cancel the parcel identified by ``parcel_id``."""
        from shipping_boxnow.exceptions import BoxNowAPIError
        from shipping_boxnow.services import BoxNowService

        try:
            shipment = BoxNowShipment.objects.select_related("order").get(
                parcel_id=parcel_id
            )
        except BoxNowShipment.DoesNotExist:
            from rest_framework.exceptions import NotFound

            raise NotFound(f"No shipment found with parcel ID {parcel_id!r}.")

        reason: str = request.data.get("reason", "")

        try:
            BoxNowService.cancel_shipment(shipment, reason=reason)
        except BoxNowAPIError as exc:
            logger.warning(
                "BoxNow cancel failed | parcel_id=%s | code=%s | msg=%s",
                parcel_id,
                exc.code,
                exc,
            )
            return Response({"detail": str(exc), "code": exc.code}, status=400)

        logger.info(
            "BoxNow parcel cancelled by admin | parcel_id=%s | user=%s",
            parcel_id,
            request.user,
        )
        return Response(
            {"status": "cancelled", "parcel_id": parcel_id},
            status=200,
        )
