"""The file response of a GDPR data export, for its owner's download
(``UserAccountViewSet.download_data_export``)."""

from __future__ import annotations

import logging
import os

from django.http import FileResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException, NotFound

from user.models.data_export import UserDataExport
from user.services.gdpr import get_export_location

logger = logging.getLogger(__name__)


class ExportExpired(APIException):
    status_code = status.HTTP_410_GONE
    default_detail = _("This export has expired.")
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
