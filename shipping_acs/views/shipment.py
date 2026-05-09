"""Public-facing ACS shipment views: label download, cancel, tracking.

* ``AcsLabelView``    — owner / admin / guest-with-uuid downloads the
                        voucher PDF.
* ``AcsCancelView``   — admin-only cancellation via ACS_Delete_Voucher.
* ``AcsTrackingView`` — owner / admin / guest reads the latest tracking
                        snapshot + events for the order.
"""

from __future__ import annotations

import io
import logging

from django.http import FileResponse
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAdminUser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.permissions import IsOwnerOrAdminOrGuest
from shipping_acs.models import AcsShipment
from shipping_acs.serializers import AcsShipmentDetailSerializer

logger = logging.getLogger(__name__)


class AcsLabelView(APIView):
    """Stream the ACS voucher label PDF.

    Permission model mirrors the BoxNow label view: authenticated
    owner, staff, or guest with a matching ``?uuid=`` query parameter
    (``IsOwnerOrAdminOrGuest`` checked against the related Order).
    """

    permission_classes = [IsOwnerOrAdminOrGuest]
    serializer_class = AcsShipmentDetailSerializer

    @extend_schema(
        operation_id="getAcsLabel",
        summary="Download ACS voucher label PDF",
        description=(
            "Fetches the ACS PDF label for the given voucher number and"
            " streams it as an attachment.  Accessible by the order"
            " owner, staff, or a guest with the order UUID."
        ),
        tags=["ACS shipments"],
        responses={200: bytes, 403: None, 404: None},
    )
    def get(self, request: Request, voucher_no: str) -> FileResponse:
        from shipping_acs.services import AcsService

        try:
            shipment = AcsShipment.objects.select_related("order").get(
                voucher_no=voucher_no
            )
        except AcsShipment.DoesNotExist as exc:
            raise NotFound(
                f"No ACS shipment found with voucher {voucher_no!r}."
            ) from exc

        self.check_object_permissions(request, shipment.order)

        pdf_bytes = AcsService.fetch_label_bytes(shipment)
        return FileResponse(
            io.BytesIO(pdf_bytes),
            content_type="application/pdf",
            as_attachment=True,
            filename=f"acs_{voucher_no}.pdf",
        )


class AcsCancelView(APIView):
    """Cancel an ACS voucher (admin-only)."""

    permission_classes = [IsAdminUser]
    serializer_class = AcsShipmentDetailSerializer

    @extend_schema(
        operation_id="cancelAcsShipment",
        summary="Cancel an ACS voucher",
        description=(
            "Admin-only.  Calls ACS_Delete_Voucher.  Only valid before "
            "the voucher is issued in a pickup list."
        ),
        tags=["ACS shipments"],
        responses={200: None, 400: None, 403: None, 404: None},
    )
    def post(self, request: Request, voucher_no: str) -> Response:
        from shipping_acs.exceptions import AcsAPIError
        from shipping_acs.services import AcsService

        try:
            shipment = AcsShipment.objects.select_related("order").get(
                voucher_no=voucher_no
            )
        except AcsShipment.DoesNotExist as exc:
            raise NotFound(
                f"No ACS shipment found with voucher {voucher_no!r}."
            ) from exc

        reason: str = request.data.get("reason", "")
        try:
            AcsService.cancel_voucher(shipment, reason=reason)
        except AcsAPIError as exc:
            logger.warning(
                "ACS cancel failed | voucher_no=%s | msg=%s",
                voucher_no,
                exc,
            )
            return Response({"detail": str(exc)}, status=400)

        return Response(
            {"status": "cancelled", "voucher_no": voucher_no},
            status=200,
        )


class AcsTrackingView(APIView):
    """Return the shipment + last 50 events for ``voucher_no``."""

    permission_classes = [IsOwnerOrAdminOrGuest]
    serializer_class = AcsShipmentDetailSerializer

    @extend_schema(
        operation_id="getAcsTracking",
        summary="Read ACS shipment tracking snapshot",
        tags=["ACS shipments"],
        responses={200: AcsShipmentDetailSerializer},
    )
    def get(self, request: Request, voucher_no: str) -> Response:
        try:
            shipment = (
                AcsShipment.objects.select_related(
                    "order", "station_destination", "pickup_list"
                )
                .prefetch_related("events")
                .get(voucher_no=voucher_no)
            )
        except AcsShipment.DoesNotExist as exc:
            raise NotFound(
                f"No ACS shipment found with voucher {voucher_no!r}."
            ) from exc

        self.check_object_permissions(request, shipment.order)
        serializer = self.serializer_class(
            shipment, context={"request": request}
        )
        return Response(serializer.data)
