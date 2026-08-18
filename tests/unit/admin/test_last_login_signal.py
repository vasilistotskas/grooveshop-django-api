"""Tests for ``admin.signals._tenant_aware_update_last_login``.

Django's stock ``update_last_login`` saves the ``User`` instance it's
handed. For a platform-staff session (``PlatformStaffBackend``) that
instance is the PUBLIC-schema row, but under a request on a tenant
host the save would otherwise run against whatever schema resolved —
this receiver reroutes that one case through ``schema_context(public)``
and defers to stock behaviour for everything else.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth.signals import user_logged_in

from admin.signals import (
    _LAST_LOGIN_DISPATCH_UID,
    _tenant_aware_update_last_login,
)
from tenant.auth_backends import PLATFORM_STAFF_BACKEND_PATH
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db


class TestTenantAwareUpdateLastLogin:
    def test_platform_staff_session_updates_last_login(self):
        staff = UserAccountFactory(is_staff=True)
        staff.backend = PLATFORM_STAFF_BACKEND_PATH
        before = staff.last_login

        _tenant_aware_update_last_login(sender=None, user=staff)

        staff.refresh_from_db()
        assert staff.last_login is not None
        assert staff.last_login != before

    def test_non_platform_session_defers_to_stock_behaviour(self):
        staff = UserAccountFactory(is_staff=True)
        staff.backend = "django.contrib.auth.backends.ModelBackend"

        with patch(
            "django.contrib.auth.models.update_last_login"
        ) as mock_stock:
            _tenant_aware_update_last_login(sender=None, user=staff)

        mock_stock.assert_called_once()

    def test_wired_in_place_of_stock_receiver(self):
        """The stock receiver is disconnected and ours takes its slot —
        connecting under the ORIGINAL dispatch_uid would otherwise be a
        silent no-op (Django skips duplicate dispatch_uid connections)."""
        receivers = [
            r
            for r in user_logged_in.receivers
            if r[0][0] == _LAST_LOGIN_DISPATCH_UID
        ]
        assert receivers, "tenant-aware receiver is not connected"

        stock_uids = [
            r
            for r in user_logged_in.receivers
            if r[0][0] == "update_last_login"
        ]
        assert not stock_uids, "stock update_last_login is still connected"
