"""A broker outage must not strand an export or half-run an erasure.

``export_user_data_task.delay`` raised after the ``UserDataExport`` row was
committed: the customer got a 500, and the next click found the PENDING row,
answered 202 and queued nothing, for ever (``expires_at`` is NULL, so the
cleanup never selected it).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from kombu.exceptions import OperationalError
from rest_framework import status
from rest_framework.test import APIClient

from tenant.models import DeferredDispatch
from user.models.data_export import UserDataExport
from user.tasks import delete_user_account_task, export_user_data_task

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_and_user():
    user = User.objects.create_user(
        email="subject@example.gr", password="pw", username="subject"
    )
    client = APIClient()
    client.force_authenticate(user=user)
    return client, user


def test_a_failed_publish_keeps_the_export_and_answers_accepted(
    client_and_user,
):
    client, user = client_and_user
    url = reverse("user-account-request-data-export", args=[user.pk])

    with patch.object(
        export_user_data_task,
        "apply_async",
        side_effect=OperationalError("broker unreachable"),
    ):
        response = client.post(url)

    assert response.status_code == status.HTTP_202_ACCEPTED
    export = UserDataExport.objects.get(user=user)
    row = DeferredDispatch.objects.get()
    assert row.task_name == export_user_data_task.name
    assert row.args == [export.id]


def test_a_failed_publish_keeps_the_erasure_queued(client_and_user):
    client, user = client_and_user
    url = reverse("user-account-delete-account", args=[user.pk])

    with (
        patch(
            "allauth.account.internal.flows.reauthentication."
            "did_recently_reauthenticate",
            return_value=True,
        ),
        patch.object(
            delete_user_account_task,
            "apply_async",
            side_effect=OperationalError("broker unreachable"),
        ),
    ):
        response = client.post(url, {"confirmation": "DELETE"}, format="json")

    assert response.status_code == status.HTTP_202_ACCEPTED
    row = DeferredDispatch.objects.get()
    assert row.task_name == delete_user_account_task.name
    assert row.args == [user.id]
