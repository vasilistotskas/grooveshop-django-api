"""Test for expired GDPR export cleanup (G0435)."""

from __future__ import annotations

import os
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from user.factories.account import UserAccountFactory
from user.models.data_export import UserDataExport
from user.services.gdpr import get_export_location
from user.tasks import cleanup_expired_data_exports


@pytest.mark.django_db
def test_cleanup_expired_data_exports(tmp_path):
    with override_settings(PRIVATE_MEDIA_ROOT=str(tmp_path)):
        location = get_export_location()
        user = UserAccountFactory()

        rel = f"{user.id}/tok.json"
        abs_path = os.path.join(location, rel)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "wb") as fh:
            fh.write(b"{}")

        expired = UserDataExport.objects.create(
            user=user,
            status=UserDataExport.Status.READY,
            file_path=rel,
            file_size=2,
            token="tok-expired",
            expires_at=timezone.now() - timedelta(days=1),
        )
        fresh = UserDataExport.objects.create(
            user=user,
            status=UserDataExport.Status.READY,
            file_path=f"{user.id}/fresh.json",
            file_size=2,
            token="tok-fresh",
            expires_at=timezone.now() + timedelta(days=1),
        )

        result = cleanup_expired_data_exports.apply().get()

    assert result["expired"] == 1

    expired.refresh_from_db()
    fresh.refresh_from_db()

    # Expired: file removed, PII bundle metadata cleared, marked EXPIRED.
    assert expired.status == UserDataExport.Status.EXPIRED
    assert expired.file_path == ""
    assert not os.path.exists(abs_path)

    # Fresh (not yet expired): untouched.
    assert fresh.status == UserDataExport.Status.READY
    assert fresh.file_path == f"{user.id}/fresh.json"
