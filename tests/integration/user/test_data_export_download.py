"""A data export reaches its owner, signed in, and nobody else."""

from __future__ import annotations

import os
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from user.factories.account import UserAccountFactory
from user.models.data_export import UserDataExport
from user.tasks import cleanup_expired_data_exports

pytestmark = pytest.mark.django_db


@pytest.fixture
def export_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "user.views.data_export.get_export_location", lambda: str(tmp_path)
    )
    return tmp_path


def make_export(owner, export_dir, **overrides) -> UserDataExport:
    path = f"{owner.pk}/bundle.json"
    os.makedirs(export_dir / str(owner.pk), exist_ok=True)
    (export_dir / path).write_bytes(b'{"profile": "theirs"}')
    fields = {
        "user": owner,
        "status": UserDataExport.Status.READY,
        "file_path": path,
        "file_size": 21,
        "expires_at": timezone.now() + timedelta(days=7),
    }
    return UserDataExport.objects.create(**{**fields, **overrides})


def download_url(owner, export) -> str:
    return reverse(
        "user-account-download-data-export",
        kwargs={"pk": owner.pk, "export_id": export.pk},
    )


def client_of(user=None) -> APIClient:
    client = APIClient()
    if user is not None:
        client.force_authenticate(user=user)
    return client


def test_the_public_token_route_is_gone(export_dir):
    make_export(UserAccountFactory(), export_dir)

    response = client_of().get("/api/v1/user/data_export/any-token/download")

    assert response.status_code == 404


def test_the_export_list_names_no_token_and_no_address(export_dir):
    owner = UserAccountFactory()
    make_export(owner, export_dir)

    response = client_of(owner).get(
        reverse("user-account-data-exports", kwargs={"pk": owner.pk})
    )

    assert response.status_code == 200
    row = response.data["results"][0]
    assert "token" not in row
    assert "download_url" not in row
    assert "downloadUrl" not in row


def test_owner_downloads_their_bundle(export_dir):
    owner = UserAccountFactory()
    export = make_export(owner, export_dir)

    response = client_of(owner).get(download_url(owner, export))

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == b'{"profile": "theirs"}'
    assert response["Content-Type"] == "application/json"
    assert "attachment" in response["Content-Disposition"]


def test_anonymous_visitor_is_refused(export_dir):
    owner = UserAccountFactory()
    export = make_export(owner, export_dir)

    response = client_of().get(download_url(owner, export))

    assert response.status_code in {401, 403}


def test_another_customer_is_refused(export_dir):
    owner = UserAccountFactory()
    export = make_export(owner, export_dir)

    response = client_of(UserAccountFactory()).get(download_url(owner, export))

    assert response.status_code in {403, 404}
    assert not getattr(response, "streaming", False)


def test_store_staff_and_superusers_are_refused(export_dir):
    owner = UserAccountFactory()
    export = make_export(owner, export_dir)
    superuser = UserAccountFactory(is_staff=True, is_superuser=True)

    response = client_of(superuser).get(download_url(owner, export))

    assert response.status_code == 404
    assert response.data["code"] == "export_not_found"
    assert not getattr(response, "streaming", False)


def test_owner_cannot_reach_another_accounts_export_through_their_own(
    export_dir,
):
    owner = UserAccountFactory()
    other = UserAccountFactory()
    export = make_export(other, export_dir)

    response = client_of(owner).get(download_url(owner, export))

    assert response.status_code == 404
    assert response.data["code"] == "export_not_found"


def test_export_not_ready_is_not_found(export_dir):
    owner = UserAccountFactory()
    export = make_export(
        owner, export_dir, status=UserDataExport.Status.PROCESSING
    )

    response = client_of(owner).get(download_url(owner, export))

    assert response.status_code == 404
    assert response.data["code"] == "export_not_ready"


def test_expired_export_is_gone_and_its_file_still_reaches_the_cleanup(
    export_dir,
):
    owner = UserAccountFactory()
    export = make_export(
        owner, export_dir, expires_at=timezone.now() - timedelta(minutes=1)
    )
    bundle = export_dir / export.file_path

    response = client_of(owner).get(download_url(owner, export))

    assert response.status_code == 410
    assert response.data["code"] == "download_expired"

    with patch(
        "user.services.gdpr.get_export_location", return_value=str(export_dir)
    ):
        cleanup_expired_data_exports()

    assert not bundle.exists()
    export.refresh_from_db()
    assert export.status == UserDataExport.Status.EXPIRED


def test_missing_file_is_not_found(export_dir):
    owner = UserAccountFactory()
    export = make_export(owner, export_dir)
    os.remove(export_dir / export.file_path)

    response = client_of(owner).get(download_url(owner, export))

    assert response.status_code == 404
    assert response.data["code"] == "export_file_missing"
