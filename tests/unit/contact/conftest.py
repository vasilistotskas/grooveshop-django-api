"""Shared fixtures for the contact-attachment tests."""

from __future__ import annotations

import pytest
from extra_settings.models import Setting

PDF_HEAD = b"%PDF-1.7\n%\xc7\xec\x8f\xa2\n"
ZIP_HEAD = b"PK\x03\x04" + bytes(26)
DWG_HEAD = b"AC1032" + bytes(26)


def pdf_bytes(size: int = 512) -> bytes:
    """A payload that SNIFFS as a PDF, padded to ``size``."""
    return PDF_HEAD + b"0" * max(0, size - len(PDF_HEAD))


@pytest.fixture
def private_tree(tmp_path, settings):
    """Point the private tree at a temp directory for one test.

    ``TenantPrivateFileSystemStorage.location`` is a property that
    re-reads this on every operation, so overriding the setting is
    enough — no storage has to be rebuilt.
    """
    root = tmp_path / "mediafiles_private"
    settings.PRIVATE_MEDIA_ROOT = str(root)
    return root


@pytest.fixture
def attachments_on(db):
    """Turn the merchant's attachment switch on, PDF only, 3 x 1 MB.

    Deliberately tighter than the shipped defaults so the size and
    type gates can be exercised without moving megabytes around.
    """
    Setting.objects.filter(name="CONTACT_ATTACHMENTS_ENABLED").update(
        value_bool=True
    )
    Setting.objects.filter(name="CONTACT_ATTACHMENTS_MAX_COUNT").update(
        value_int=3
    )
    Setting.objects.filter(name="CONTACT_ATTACHMENTS_MAX_MB").update(
        value_int=1
    )
    Setting.objects.filter(name="CONTACT_ATTACHMENTS_TYPES").update(
        value_string="application/pdf"
    )
