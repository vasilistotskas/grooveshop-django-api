"""An attachment's bytes must land in the private per-tenant tree.

Two things this file exists to keep true, because a regression in
either turns an anonymous upload endpoint into a public file host:

1. The location is resolved per operation, so the schema is the live
   one and not whatever was bound when the model class was defined.
2. The stored path is derived from the row's UUID, never from the
   uploader's filename.
"""

from __future__ import annotations

import re
import uuid
from unittest import mock

import pytest
from django.db import connection
from django.utils import timezone

from contact.models import ContactAttachment
from contact.storage import attachment_upload_to, private_attachment_storage
from tenant.storage import TenantPrivateFileSystemStorage, private_media_root


def _schema_segment(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


class TestPrivateAttachmentStorage:
    def test_field_resolved_the_callable_to_the_dynamic_storage(self):
        storage = ContactAttachment._meta.get_field("file").storage
        assert isinstance(storage, TenantPrivateFileSystemStorage)

    def test_location_tracks_the_active_schema(self):
        storage = private_attachment_storage()
        with mock.patch.object(connection, "schema_name", "acme"):
            assert _schema_segment(storage.location) == "acme"
        with mock.patch.object(connection, "schema_name", "beta"):
            assert _schema_segment(storage.location) == "beta"

    def test_field_storage_is_not_frozen_to_public(self):
        storage = ContactAttachment._meta.get_field("file").storage
        with mock.patch.object(connection, "schema_name", "webside"):
            assert _schema_segment(storage.location) == "webside"

    def test_location_lives_under_the_private_root(self):
        storage = private_attachment_storage()
        with mock.patch.object(connection, "schema_name", "acme"):
            assert storage.location.startswith(private_media_root())

    def test_the_private_root_is_not_inside_the_public_one(self):
        """A private file under MEDIA_ROOT would be a public file."""
        from django.conf import settings

        private = private_media_root().replace("\\", "/").rstrip("/")
        public = settings.MEDIA_ROOT.replace("\\", "/").rstrip("/")
        assert not private.startswith(public + "/")

    def test_there_is_no_url_to_hand_out(self):
        """``FileSystemStorage`` would otherwise inherit MEDIA_URL."""
        storage = private_attachment_storage()
        assert storage.base_url is None

    def test_asking_for_a_url_raises(self):
        """A file here has no URL, and reaching for one is a bug."""
        storage = private_attachment_storage()
        with pytest.raises(ValueError, match="not accessible via a URL"):
            storage.url("contact/2026/03/x.pdf")


class TestAttachmentUploadTo:
    def _row(self, content_type="application/pdf"):
        return ContactAttachment(
            uuid=uuid.UUID("11111111-2222-3333-4444-555555555555"),
            created_at=timezone.datetime(
                2026, 3, 9, tzinfo=timezone.get_current_timezone()
            ),
            content_type=content_type,
        )

    def test_path_is_month_prefixed_and_named_by_uuid(self):
        path = attachment_upload_to(self._row(), "whatever.pdf").replace(
            "\\", "/"
        )
        assert path == (
            "contact/2026/03/11111111-2222-3333-4444-555555555555.pdf"
        )

    def test_the_uploaders_filename_never_reaches_the_path(self):
        hostile = "../../../../etc/cron.d/evil;rm -rf /.pdf"
        path = attachment_upload_to(self._row(), hostile).replace("\\", "/")
        assert ".." not in path
        assert "etc" not in path
        assert re.fullmatch(r"contact/\d{4}/\d{2}/[0-9a-f-]{36}\.pdf", path)

    def test_extension_comes_from_the_sniffed_type(self):
        path = attachment_upload_to(
            self._row(content_type="application/zip"), "drawing.pdf"
        )
        assert path.endswith(".zip")

    def test_an_unmappable_type_still_gets_a_path(self):
        path = attachment_upload_to(
            self._row(content_type="application/x-nonsense"), "x"
        )
        assert path.endswith(".bin")

    def test_a_row_with_no_timestamp_yet_uses_now(self):
        row = ContactAttachment(
            uuid=uuid.uuid4(), content_type="application/pdf"
        )
        path = attachment_upload_to(row, "x.pdf").replace("\\", "/")
        assert path.startswith(f"contact/{timezone.now():%Y}/")
