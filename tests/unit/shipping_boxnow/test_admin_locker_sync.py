"""The locker sync is a list action: it syncs every locker, answers
with a redirect back to the locker list, and reports failures there."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from shipping_boxnow.admin import BoxNowLockerAdmin
from shipping_boxnow.models import BoxNowLocker


def _request():
    request = RequestFactory().get("/")
    request.user = get_user_model()(id=1, username="staff")
    return request


@pytest.fixture
def locker_admin():
    return BoxNowLockerAdmin(BoxNowLocker, AdminSite())


def test_it_is_a_list_action(locker_admin):
    assert "sync_from_boxnow" in locker_admin.actions_list
    assert "sync_from_boxnow" not in (locker_admin.actions or [])


@pytest.mark.parametrize("fails", [False, True])
def test_it_redirects_to_the_locker_list(locker_admin, fails):
    sync = "shipping_boxnow.services.BoxNowService.sync_lockers"
    with (
        patch(sync, side_effect=RuntimeError("down") if fails else None) as run,
        patch("django.contrib.messages.success"),
        patch("django.contrib.messages.error") as error,
    ):
        run.return_value = {}
        response = locker_admin.sync_from_boxnow(_request())

    assert response.status_code == 302
    assert response.url.endswith("/shipping_boxnow/boxnowlocker/")
    assert error.called is fails
