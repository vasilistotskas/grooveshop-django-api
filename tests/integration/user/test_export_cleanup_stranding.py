"""A failed removal must not orphan the exported personal data.

`cleanup_expired_data_exports` deletes the JSON bundle of an expired
right-of-access export and blanks `file_path` on the row. The blanking
used to happen whether or not the file went: an `OSError` from the
private-media PVC was logged as a warning and then the only reference to
that file on disk was erased, leaving the subject's complete personal
data past its TTL with nothing that could ever find it again — and no
subsequent run to retry, because the row was now `EXPIRED`.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from user.models.data_export import UserDataExport
from user.tasks import cleanup_expired_data_exports

User = get_user_model()


@pytest.fixture
def expired_export(db, tmp_path):
    user = User.objects.create_user(
        email="subject@example.gr", password="pw", username="subject"
    )
    bundle = tmp_path / "export.json"
    bundle.write_text('{"email": "subject@example.gr"}', encoding="utf-8")

    export = UserDataExport.objects.create(
        user=user,
        status=UserDataExport.Status.READY,
        file_path=bundle.name,
        file_size=bundle.stat().st_size,
        expires_at=timezone.now() - timezone.timedelta(days=1),
    )
    return export, bundle, str(tmp_path)


def test_a_removable_bundle_is_deleted_and_the_row_expired(expired_export):
    export, bundle, location = expired_export

    with patch("user.services.gdpr.get_export_location", return_value=location):
        result = cleanup_expired_data_exports()

    assert result["expired"] == 1
    assert result["stranded"] == 0
    assert not bundle.exists()
    export.refresh_from_db()
    assert export.status == UserDataExport.Status.EXPIRED
    assert export.file_path == ""


def test_a_bundle_that_will_not_delete_keeps_its_only_reference(
    expired_export,
):
    export, bundle, location = expired_export

    with (
        patch("user.services.gdpr.get_export_location", return_value=location),
        patch("os.remove", side_effect=OSError("read-only volume")),
    ):
        result = cleanup_expired_data_exports()

    assert result["stranded"] == 1
    assert result["expired"] == 0
    assert bundle.exists(), "the file is still there — that is the point"

    export.refresh_from_db()
    assert export.file_path == bundle.name, (
        "blanking file_path would leave the bundle unreachable forever"
    )
    assert export.status != UserDataExport.Status.EXPIRED, (
        "the row must stay pending so the next run retries"
    )


def test_the_next_run_finishes_what_the_failed_one_could_not(expired_export):
    export, bundle, location = expired_export

    with (
        patch("user.services.gdpr.get_export_location", return_value=location),
        patch("os.remove", side_effect=OSError("read-only volume")),
    ):
        cleanup_expired_data_exports()

    with patch("user.services.gdpr.get_export_location", return_value=location):
        result = cleanup_expired_data_exports()

    assert result["expired"] == 1
    assert not bundle.exists()
    export.refresh_from_db()
    assert export.status == UserDataExport.Status.EXPIRED


def test_a_bundle_already_gone_from_disk_still_expires_the_row(expired_export):
    """The terminal case: nothing to remove is success, not a strand."""
    export, bundle, location = expired_export
    os.remove(bundle)

    with patch("user.services.gdpr.get_export_location", return_value=location):
        result = cleanup_expired_data_exports()

    assert result["expired"] == 1
    assert result["stranded"] == 0
    export.refresh_from_db()
    assert export.status == UserDataExport.Status.EXPIRED
