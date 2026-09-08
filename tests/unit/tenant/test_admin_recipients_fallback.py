"""The platform fallback for ops-alert recipients must not raise.

``tenant_admin_recipients`` unpacked each ``settings.ADMINS`` entry into
``(_name, email)``. Django 6 made a list of plain address STRINGS the
correct format — ``mail_admins`` now warns ``RemovedInDjango70Warning``
on the old ``(name, address)`` pairs — and this project's setting is
built as ``[email for email in [getenv("ADMIN_EMAIL", "")] if email]``.
Unpacking a string walks its characters, so any address longer than two
characters raised ``ValueError: too many values to unpack``.

Verified in production on 2026-09-08: all four tenants carry their own
``contact_email``/``owner_email`` and returned before the fallback, so
only the platform path was affected. It was invisible because every
caller wraps this in try/except — a mail failure must never mask the
carrier error it is reporting — so the alert was swallowed, not surfaced.
"""

from __future__ import annotations

import pytest
from django.test import override_settings

from tenant.credentials import tenant_admin_recipients

pytestmark = pytest.mark.django_db


@override_settings(ADMINS=["ops@example.com"])
def test_plain_address_strings_are_returned():
    assert tenant_admin_recipients() == ["ops@example.com"]


@override_settings(ADMINS=["ops@example.com", "second@example.com"])
def test_every_address_is_returned():
    assert tenant_admin_recipients() == [
        "ops@example.com",
        "second@example.com",
    ]


@override_settings(ADMINS=[])
def test_no_configured_admin_is_not_an_error():
    """``ADMIN_EMAIL`` unset is a supported deployment, not a failure.

    ``send_ops_alert`` turns an empty list into ``False`` and logs.
    """
    assert tenant_admin_recipients() == []


@override_settings(ADMINS=["ops@example.com"])
def test_the_tenant_wins_over_the_platform_fallback():
    """Operational alerts belong to the store's operators.

    They are the people who can fix an address or reconcile a payout;
    the platform owner is only the fallback.
    """
    from unittest.mock import patch

    with patch(
        "tenant.credentials._get_tenant_field",
        side_effect=lambda field, *a, **k: (
            "shop@example.com" if field == "contact_email" else ""
        ),
    ):
        assert tenant_admin_recipients() == ["shop@example.com"]
