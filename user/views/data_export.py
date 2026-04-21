"""Download endpoint for GDPR data exports.

Public by design — the path carries a random 64-char token that is
tied to a single ``UserDataExport`` row with a 7-day expiry. No
session or Knox token is required because the user opens the link
from an email client that may not have cookies for the site.
"""

from __future__ import annotations

import logging
import os

from django.conf import settings
from django.http import FileResponse, Http404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from user.models.data_export import UserDataExport

logger = logging.getLogger(__name__)


class UserDataExportDownloadView(APIView):
    """``GET /api/v1/user/data_export/{token}/download``."""

    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(
        operation_id="downloadUserDataExport",
        tags=["User Accounts"],
        summary=_("Download a GDPR data export bundle"),
        description=_(
            "Returns the JSON bundle produced by the export task. The "
            "token is single-scope, tied to one UserDataExport row, and "
            "expires 7 days after the export was produced."
        ),
        responses={200: None, 404: None, 410: None},
    )
    def get(self, request, token: str):
        try:
            export = UserDataExport.objects.select_related("user").get(
                token=token
            )
        except UserDataExport.DoesNotExist as e:
            raise Http404(_("Export not found.")) from e

        if not export.is_ready:
            raise Http404(_("Export not ready."))

        if export.expires_at and export.expires_at < timezone.now():
            if export.status != UserDataExport.Status.EXPIRED:
                UserDataExport.objects.filter(pk=export.pk).update(
                    status=UserDataExport.Status.EXPIRED
                )
            return Response(
                {"detail": _("This download link has expired.")},
                status=410,
            )

        location = os.path.join(
            settings.MEDIA_ROOT or "mediafiles", "_gdpr_exports"
        )
        abs_path = os.path.join(location, export.file_path)
        if not os.path.exists(abs_path):
            logger.error(
                "Export %s is READY but file missing at %s",
                export.pk,
                abs_path,
            )
            raise Http404(_("Export file missing."))

        response = FileResponse(
            open(abs_path, "rb"),  # noqa: SIM115 — FileResponse owns the fh
            content_type="application/json",
            as_attachment=True,
            filename=f"grooveshop-data-export-{export.user_id}.json",
        )
        return response
