"""The gate: what a store accepts, and what the bytes actually are.

Everything here is about refusing a file the merchant did not ask for,
which is the half of the attachment feature an anonymous uploader can
reach.
"""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from extra_settings.models import Setting

from contact.attachments import (
    AttachmentPolicy,
    sanitize_filename,
    sniff_content_type,
)

PDF_BYTES = b"%PDF-1.7\n%\xc7\xec\x8f\xa2\n1 0 obj\n<<>>\nendobj\n"
#: An AutoCAD DWG header (R2018). Its whole signature is the version
#: tag, which is exactly why the sniff must look at bytes.
DWG_BYTES = b"AC1032" + bytes(26)
ZIP_BYTES = b"PK\x03\x04" + bytes(26)
#: ASCII DXF: a real CAD format with NO magic number, so the sniffer
#: cannot confirm it and the gate must refuse it.
DXF_BYTES = b"  0\r\nSECTION\r\n  2\r\nHEADER\r\n"


def _set(name: str, **value) -> None:
    Setting.objects.filter(name=name).update(**value)


@pytest.mark.django_db
class TestAttachmentPolicy:
    def test_defaults_are_closed(self):
        """A store that never asked for attachments does not host one."""
        policy = AttachmentPolicy()
        assert policy.enabled is False

    def test_reads_the_merchants_rows(self):
        _set("CONTACT_ATTACHMENTS_ENABLED", value_bool=True)
        _set("CONTACT_ATTACHMENTS_MAX_COUNT", value_int=5)
        _set("CONTACT_ATTACHMENTS_MAX_MB", value_int=20)
        _set(
            "CONTACT_ATTACHMENTS_TYPES",
            value_string="application/pdf, application/zip",
        )

        policy = AttachmentPolicy()

        assert policy.enabled is True
        assert policy.max_count == 5
        assert policy.max_bytes == 20 * 1024 * 1024
        assert policy.max_megabytes == 20
        assert policy.allowed_types == ("application/pdf", "application/zip")

    def test_size_is_capped_in_code(self):
        """A merchant cannot spend the operator's disk and bandwidth."""
        _set("CONTACT_ATTACHMENTS_MAX_MB", value_int=2048)
        assert (
            AttachmentPolicy().max_bytes == AttachmentPolicy.MAX_BYTES_CEILING
        )

    def test_count_and_size_have_a_floor(self):
        _set("CONTACT_ATTACHMENTS_MAX_COUNT", value_int=0)
        _set("CONTACT_ATTACHMENTS_MAX_MB", value_int=0)
        policy = AttachmentPolicy()
        assert policy.max_count == 1
        assert policy.max_bytes == 1024 * 1024

    def test_blank_type_list_falls_back_to_documents(self):
        _set("CONTACT_ATTACHMENTS_TYPES", value_string="   ")
        assert AttachmentPolicy().allowed_types == ("application/pdf",)

    def test_accepts_is_case_insensitive_and_refuses_none(self):
        _set("CONTACT_ATTACHMENTS_TYPES", value_string="application/pdf")
        policy = AttachmentPolicy()
        assert policy.accepts("APPLICATION/PDF") is True
        assert policy.accepts("application/zip") is False
        # An unconfirmed format is never accepted.
        assert policy.accepts(None) is False


class TestSniffContentType:
    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (PDF_BYTES, "application/pdf"),
            (DWG_BYTES, "image/vnd.dwg"),
            (ZIP_BYTES, "application/zip"),
        ],
    )
    def test_confirms_container_formats(self, payload, expected):
        upload = SimpleUploadedFile("x.bin", payload)
        assert sniff_content_type(upload) == expected

    def test_a_text_format_cannot_be_confirmed(self):
        """ASCII DXF has no signature — refused rather than trusted."""
        upload = SimpleUploadedFile("plan.dxf", DXF_BYTES)
        assert sniff_content_type(upload) is None

    def test_the_extension_and_declared_type_are_ignored(self):
        upload = SimpleUploadedFile(
            "invoice.pdf", ZIP_BYTES, content_type="application/pdf"
        )
        assert sniff_content_type(upload) == "application/zip"

    def test_the_stream_is_left_rewound(self):
        upload = SimpleUploadedFile("x.pdf", PDF_BYTES)
        sniff_content_type(upload)
        assert upload.read() == PDF_BYTES

    def test_an_empty_or_tiny_file_is_unrecognised(self):
        assert sniff_content_type(io.BytesIO(b"")) is None
        assert sniff_content_type(io.BytesIO(b"hi")) is None


class TestSanitizeFilename:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("plan.pdf", "plan.pdf"),
            # Directory components never survive.
            ("../../etc/passwd", "passwd"),
            (r"C:\Users\me\Desktop\plan.pdf", "plan.pdf"),
            ("/absolute/plan.pdf", "plan.pdf"),
            # Control characters, including a CR that could split a
            # Content-Disposition header.
            ("pl\x00an\r\n.pdf", "plan.pdf"),
            ("  spaced   out.pdf  ", "spaced out.pdf"),
            # Never empty: the admin has to render something.
            ("", "attachment"),
            (None, "attachment"),
            ("...", "attachment"),
        ],
    )
    def test_hygiene(self, given, expected):
        assert sanitize_filename(given) == expected

    def test_length_is_bounded(self):
        assert len(sanitize_filename("a" * 5000)) == 255
