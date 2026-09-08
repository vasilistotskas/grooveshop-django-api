"""The admin download is the ONLY reader of an attachment's bytes.

There is no public URL, signed or otherwise, so everything that
protects the file is in this one view: the admin login, the model's
``view`` permission, the scan-verdict check, and the headers that stop
a browser rendering an anonymous stranger's file in a staff session.
"""

from __future__ import annotations

import hashlib

import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.http import Http404
from django.test import RequestFactory
from django.utils import timezone

from contact.admin import ContactAdmin, ContactAttachmentInline
from contact.factories import ContactFactory
from contact.models import Contact, ContactAttachment
from tests.unit.contact.conftest import pdf_bytes
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def contact_admin():
    from django.contrib import admin as django_admin

    return ContactAdmin(Contact, django_admin.site)


@pytest.fixture
def attachment(private_tree):
    payload = pdf_bytes(128)
    row = ContactAttachment(
        contact=ContactFactory(),
        original_name="Σχέδιο Α1.pdf",
        content_type="application/pdf",
        size=len(payload),
        checksum=hashlib.sha256(payload).hexdigest(),
        claim_deadline=timezone.now(),
        scan_status=ContactAttachment.ScanStatus.CLEAN,
        scanned_at=timezone.now(),
    )
    row.file.save("x.pdf", ContentFile(payload), save=False)
    row.save()
    return row


def _reader(**perms):
    user = UserAccountFactory()
    if perms.get("can_view"):
        user.user_permissions.add(
            Permission.objects.get(codename="view_contactattachment")
        )
    return user


def _request(user):
    request = RequestFactory().get("/admin/contact/contact/attachment/x/")
    request.user = user
    return request


class TestAttachmentDownloadView:
    def test_a_reader_without_the_view_permission_is_refused(
        self, contact_admin, attachment
    ):
        """Being logged into the admin is not enough on its own."""
        with pytest.raises(PermissionDenied):
            contact_admin.attachment_download_view(
                _request(_reader()), attachment.uuid
            )

    def test_a_reader_with_it_gets_the_bytes(self, contact_admin, attachment):
        response = contact_admin.attachment_download_view(
            _request(_reader(can_view=True)), attachment.uuid
        )

        assert response.status_code == 200
        assert b"".join(response.streaming_content) == pdf_bytes(128)

    def test_the_headers_refuse_inline_rendering(
        self, contact_admin, attachment
    ):
        response = contact_admin.attachment_download_view(
            _request(_reader(can_view=True)), attachment.uuid
        )

        # Never the sniffed type: octet-stream cannot be rendered.
        assert response["Content-Type"] == "application/octet-stream"
        assert response["X-Content-Type-Options"] == "nosniff"
        disposition = response["Content-Disposition"]
        assert disposition.startswith("attachment;")
        # The uploader's own name comes back, RFC 5987-encoded, so a
        # Greek filename survives without a header-splitting risk.
        assert "filename*=utf-8''" in disposition.lower()
        assert not set(chr(13) + chr(10)) & set(disposition)

    @pytest.mark.parametrize(
        "withheld",
        [
            ContactAttachment.ScanStatus.PENDING,
            ContactAttachment.ScanStatus.ERROR,
            ContactAttachment.ScanStatus.INFECTED,
        ],
    )
    def test_a_verdict_that_never_cleared_is_withheld(
        self, contact_admin, attachment, withheld
    ):
        """A scan that did not say CLEAN does not open the file."""
        ContactAttachment.objects.filter(pk=attachment.pk).update(
            scan_status=withheld
        )

        with pytest.raises(Http404):
            contact_admin.attachment_download_view(
                _request(_reader(can_view=True)), attachment.uuid
            )

    def test_a_purged_row_has_nothing_to_serve(self, contact_admin, attachment):
        attachment.file.delete(save=True)
        with pytest.raises(Http404):
            contact_admin.attachment_download_view(
                _request(_reader(can_view=True)), attachment.uuid
            )

    def test_an_unknown_id_is_a_404(self, contact_admin, private_tree):
        import uuid as uuid_module

        with pytest.raises(Http404):
            contact_admin.attachment_download_view(
                _request(_reader(can_view=True)), uuid_module.uuid4()
            )

    def test_the_route_is_registered(self, contact_admin):
        names = {getattr(url, "name", None) for url in contact_admin.get_urls()}
        assert "contact_attachment_download" in names


class TestAttachmentInline:
    def test_nothing_can_be_added_by_hand(self, contact_admin):
        inline = ContactAttachmentInline(Contact, contact_admin.admin_site)
        assert inline.has_add_permission(_request(_reader())) is False

    def test_every_column_is_read_only(self, contact_admin):
        inline = ContactAttachmentInline(Contact, contact_admin.admin_site)
        assert set(inline.fields) == set(inline.readonly_fields)

    def test_a_downloadable_row_renders_a_link(self, contact_admin, attachment):
        inline = ContactAttachmentInline(Contact, contact_admin.admin_site)
        rendered = str(inline.download(attachment))
        assert str(attachment.uuid) in rendered
        assert "Σχέδιο Α1.pdf" in rendered

    def test_a_withheld_row_renders_no_link(self, contact_admin, attachment):
        attachment.scan_status = ContactAttachment.ScanStatus.INFECTED
        inline = ContactAttachmentInline(Contact, contact_admin.admin_site)
        rendered = str(inline.download(attachment))
        assert "href" not in rendered
        assert "withheld" in rendered

    def test_a_purged_row_says_so(self, contact_admin, attachment):
        attachment.file.delete(save=True)
        inline = ContactAttachmentInline(Contact, contact_admin.admin_site)
        rendered = str(inline.download(attachment))
        assert "href" not in rendered
        assert "deleted" in rendered

    def test_the_scan_state_is_labelled(self, contact_admin, attachment):
        inline = ContactAttachmentInline(Contact, contact_admin.admin_site)
        value, label = inline.scan_state(attachment)
        assert value == "CLEAN"
        assert str(label) == "Clean"

    def test_the_size_is_human_readable(self, contact_admin, attachment):
        inline = ContactAttachmentInline(Contact, contact_admin.admin_site)
        assert "byte" in str(inline.size_display(attachment)).lower()


class TestModelHelpers:
    @pytest.mark.parametrize(
        ("state", "downloadable"),
        [
            (ContactAttachment.ScanStatus.CLEAN, True),
            (ContactAttachment.ScanStatus.SKIPPED, True),
            (ContactAttachment.ScanStatus.PENDING, False),
            (ContactAttachment.ScanStatus.ERROR, False),
            (ContactAttachment.ScanStatus.INFECTED, False),
        ],
    )
    def test_is_downloadable(self, state, downloadable):
        row = ContactAttachment(scan_status=state)
        assert row.is_downloadable() is downloadable

    @pytest.mark.parametrize(
        ("content_type", "suffix"),
        [
            ("application/pdf", ".pdf"),
            ("application/zip", ".zip"),
            ("", ".bin"),
            ("application/x-nonsense", ".bin"),
        ],
    )
    def test_extension_comes_from_the_sniffed_type(self, content_type, suffix):
        assert ContactAttachment(content_type=content_type).extension() == (
            suffix
        )

    def test_str_names_the_file_and_its_size(self):
        row = ContactAttachment(original_name="plan.pdf", size=12)
        assert str(row) == "plan.pdf (12 B)"
