"""Download endpoint for GDPR data exports.

Public by design — the path carries a random 64-char token that is
tied to a single ``UserDataExport`` row with a 7-day expiry. No
session or Knox token is required because the user opens the link
from an email client that may not have cookies for the site.
"""

from __future__ import annotations

import logging
import os

from django.http import FileResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from user.models.data_export import UserDataExport
from user.services.gdpr import get_export_location

logger = logging.getLogger(__name__)


class ExportExpired(APIException):
    status_code = status.HTTP_410_GONE
    default_detail = _("This download link has expired.")
    default_code = "download_expired"


def export_file_response(export: UserDataExport) -> FileResponse:
    """The bundle of *export*, or the error that says why there is none."""
    if not export.is_ready:
        raise NotFound(_("Export not ready."), code="export_not_ready")

    # Only ``cleanup_expired_data_exports`` marks a row expired: it skips the
    # rows that are, so marking one here would leave its file on disk for good.
    if export.expires_at and export.expires_at < timezone.now():
        raise ExportExpired

    abs_path = os.path.join(get_export_location(), export.file_path)
    if not os.path.exists(abs_path):
        logger.error(
            "Export %s is READY but file missing at %s",
            export.pk,
            abs_path,
        )
        raise NotFound(_("Export file missing."), code="export_file_missing")

    return FileResponse(
        open(abs_path, "rb"),
        content_type="application/json",
        as_attachment=True,
        filename=f"data-export-{export.user_id}.json",
    )


class UserDataExportDownloadView(APIView):
    """``GET /api/v1/user/data_export/{token}/download``."""

    permission_classes = [AllowAny]
    authentication_classes = []

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
            raise NotFound(
                _("Export not found."), code="export_not_found"
            ) from e
        return export_file_response(export)
