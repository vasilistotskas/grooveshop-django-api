"""A file reference must resolve to a file, or be reported.

The failure this guards is silent by construction: a ``FileField``
holding a name whose bytes are gone is still a valid row, still
serialises, and still renders — the storefront's ``UAvatar`` falls back
to the ``alt`` initials — so the only symptom is a 404 per render in a
log nobody reads. Nine ``webside`` users were in exactly that state on
2026-09-22.
"""

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command

from core.management.commands.media_reference_audit import (
    scan_current_schema,
    tenant_file_fields,
)
from user.factories.account import UserAccountFactory
from user.models.account import UserAccount


@pytest.fixture
def user_image_pair():
    """Just the ``UserAccount.image`` pair, so a scan stays scoped."""
    return [
        (model, fields)
        for model, fields in tenant_file_fields()
        if model is UserAccount
    ]


class TestFieldDiscovery:
    def test_finds_the_user_avatar_field(self):
        pairs = {
            model: [f.name for f in fields]
            for model, fields in tenant_file_fields()
        }
        assert UserAccount in pairs
        assert "image" in pairs[UserAccount]

    def test_yields_no_abstract_or_proxy_models(self):
        for model, _fields in tenant_file_fields():
            assert not model._meta.abstract
            assert not model._meta.proxy

    def test_every_reported_field_is_a_file_field(self):
        from django.db import models

        for _model, fields in tenant_file_fields():
            assert fields
            for field in fields:
                assert isinstance(field, models.FileField)


@pytest.mark.django_db
class TestScan:
    def test_present_file_is_not_dangling(self, user_image_pair):
        user = UserAccountFactory()
        assert user.image.storage.exists(user.image.name)

        audit = scan_current_schema(user_image_pair, schema="t")

        assert audit.checked >= 1
        assert [d for d in audit.dangling if d.pk == user.pk] == []

    def test_missing_file_is_reported(self, user_image_pair):
        user = UserAccountFactory()
        stored = user.image.name
        user.image.storage.delete(stored)

        audit = scan_current_schema(user_image_pair, schema="t")

        mine = [d for d in audit.dangling if d.pk == user.pk]
        assert len(mine) == 1
        assert mine[0].field_name == "image"
        assert mine[0].stored_name == stored
        assert mine[0].model_label == "user.UserAccount"

    def test_scan_without_clear_leaves_the_row_alone(self, user_image_pair):
        user = UserAccountFactory()
        stored = user.image.name
        user.image.storage.delete(stored)

        scan_current_schema(user_image_pair, schema="t")

        user.refresh_from_db()
        assert user.image.name == stored

    def test_clear_blanks_only_the_dangling_reference(self, user_image_pair):
        broken = UserAccountFactory()
        intact = UserAccountFactory()
        broken.image.storage.delete(broken.image.name)
        intact_name = intact.image.name

        audit = scan_current_schema(user_image_pair, schema="t", clear=True)

        assert audit.cleared >= 1
        broken.refresh_from_db()
        intact.refresh_from_db()
        assert not broken.image
        assert intact.image.name == intact_name

    def test_cleared_row_yields_the_empty_media_path(self, user_image_pair):
        """The contract the storefront fallback depends on."""
        user = UserAccountFactory()
        user.image.storage.delete(user.image.name)

        scan_current_schema(user_image_pair, schema="t", clear=True)

        user.refresh_from_db()
        assert user.main_image_path == ""

    def test_clearing_is_idempotent(self, user_image_pair):
        user = UserAccountFactory()
        user.image.storage.delete(user.image.name)

        scan_current_schema(user_image_pair, schema="t", clear=True)
        second = scan_current_schema(user_image_pair, schema="t", clear=True)

        assert [d for d in second.dangling if d.pk == user.pk] == []
        assert second.cleared == 0

    def test_empty_reference_is_not_counted(self, user_image_pair):
        UserAccountFactory(image=None)

        audit = scan_current_schema(user_image_pair, schema="t")

        assert audit.dangling == []

    def test_a_file_restored_under_the_same_name_clears_the_finding(
        self, user_image_pair
    ):
        user = UserAccountFactory()
        stored = user.image.name
        user.image.storage.delete(stored)
        assert scan_current_schema(user_image_pair, schema="t").dangling

        user.image.storage.save(stored, ContentFile(b"restored"))

        audit = scan_current_schema(user_image_pair, schema="t")
        assert [d for d in audit.dangling if d.pk == user.pk] == []


@pytest.mark.django_db
class TestCommand:
    def test_unknown_schema_is_rejected(self):
        from django.core.management.base import CommandError

        with pytest.raises(CommandError, match="Unknown schema"):
            call_command("media_reference_audit", "--schema", "nope-xyz")
