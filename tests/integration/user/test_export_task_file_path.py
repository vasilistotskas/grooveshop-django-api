"""Where the export task writes a bundle, and that a rerun leaves no orphan."""

from __future__ import annotations

from pathlib import Path

import pytest

from user.factories.account import UserAccountFactory
from user.models.data_export import UserDataExport
from user.services.gdpr import get_export_location
from user.tasks import export_user_data_task

pytestmark = pytest.mark.django_db


@pytest.fixture
def private_root(settings, tmp_path):
    settings.PRIVATE_MEDIA_ROOT = str(tmp_path)
    return tmp_path


def bundles() -> list[Path]:
    return sorted(Path(get_export_location()).rglob("*.json"))


def test_a_new_export_is_named_by_its_uuid(private_root):
    user = UserAccountFactory(num_addresses=0)
    export = UserDataExport.objects.create(user=user)

    export_user_data_task(export.id)

    export.refresh_from_db()
    assert export.file_path == f"{user.pk}/{export.uuid}.json"
    assert bundles() == [Path(get_export_location()) / export.file_path]


def test_a_rerun_writes_the_file_the_row_already_names(private_root):
    user = UserAccountFactory(num_addresses=0)
    named_by_token = f"{user.pk}/a-token.json"
    export = UserDataExport.objects.create(user=user, file_path=named_by_token)

    export_user_data_task(export.id)

    export.refresh_from_db()
    assert export.file_path == named_by_token
    assert bundles() == [Path(get_export_location()) / named_by_token]
