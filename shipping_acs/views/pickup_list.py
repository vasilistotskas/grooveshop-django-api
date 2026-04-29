"""Admin-only ACS pickup-list views.

* ``AcsPickupListIssueView`` — manual override for the daily Celery
  beat task; calls the same service method.
* ``AcsPickupListManifestView`` — streams the manifest PDF for a
  specific pickup list.
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

from shipping_acs.models import AcsPickupList
from shipping_acs.serializers import AcsPickupListSerializer

logger = logging.getLogger(__name__)


class AcsPickupListIssueView(APIView):
    """Issue today's ACS pickup list on demand."""

    permission_classes = [IsAdminUser]

    @extend_schema(
        operation_id="issueAcsPickupList",
        summary="Issue today's ACS pickup list",
        description=(
            "Admin override for the daily Celery beat task. Calls "
            "AcsService.issue_daily_pickup_list. Returns the new "
            "pickup list, or 204 when no candidate vouchers exist."
        ),
        request=None,
        responses={
            201: AcsPickupListSerializer,
            204: None,
            403: None,
        },
        tags=["ACS pickup lists"],
    )
    def post(self, request: Request) -> Response:
        from shipping_acs.services import AcsService

        pickup_list = AcsService.issue_daily_pickup_list(
            issued_by_id=request.user.id
            if request.user.is_authenticated
            else None
        )
        if pickup_list is None:
            return Response(status=204)
        serializer = AcsPickupListSerializer(pickup_list)
        return Response(serializer.data, status=201)


class AcsPickupListManifestView(APIView):
    """Download the manifest PDF for a specific pickup list."""

    permission_classes = [IsAdminUser]

    @extend_schema(
        operation_id="getAcsPickupListManifest",
        summary="Download ACS pickup-list manifest PDF",
        tags=["ACS pickup lists"],
        responses={200: bytes, 404: None},
    )
    def get(self, request: Request, pickup_list_no: str) -> FileResponse:
        from shipping_acs.services import AcsService

        try:
            pickup_list = AcsPickupList.objects.get(
                pickup_list_no=pickup_list_no
            )
        except AcsPickupList.DoesNotExist as exc:
            raise NotFound(
                f"No ACS pickup list with number {pickup_list_no!r}."
            ) from exc

        pdf_bytes = AcsService.fetch_pickup_list_pdf(pickup_list)
        return FileResponse(
            io.BytesIO(pdf_bytes),
            content_type="application/pdf",
            as_attachment=True,
            filename=f"acs_pickup_list_{pickup_list_no}.pdf",
        )
