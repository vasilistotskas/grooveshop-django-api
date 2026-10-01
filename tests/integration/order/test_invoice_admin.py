"""The invoice archive is read-only for everyone.

Greek tax law allows no edits to an issued invoice and no gaps in the
register; a deleted row is a gap, so superusers do not delete either.
"""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.test import RequestFactory

from order.models.invoice import Invoice
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


def test_no_one_edits_or_deletes_an_invoice():
    request = RequestFactory().get("/")
    request.user = UserAccountFactory(admin=True)
    invoice_admin = admin.site._registry[Invoice]

    assert not invoice_admin.has_delete_permission(request)
    assert not invoice_admin.has_change_permission(request)
    assert not invoice_admin.has_add_permission(request)
