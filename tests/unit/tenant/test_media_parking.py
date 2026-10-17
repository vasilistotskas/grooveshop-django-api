"""A suspended store's images stop being served, and come back on reactivation.

Suspension parks ``MEDIA_ROOT/{schema}`` at ``MEDIA_ROOT/.suspended/{schema}``
(the static file server denies dot-segment paths, and ``.suspended`` is not a
valid schema), then flushes media-stream and purges the edge. Reactivation
moves it back and flushes the placeholders that were cached meanwhile.
"""

from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from django.utils import timezone

from tenant.media_parking import park_tenant_media, restore_tenant_media
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db

PLATFORM_ZONE = "a" * 32
OWN_ZONE = "b" * 32


@pytest.fixture
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


def _uploads(media_root, schema):
    uploads = media_root / schema / "uploads"
    uploads.mkdir(parents=True)
    (uploads / "logo.png").write_text("img")
    return uploads


class TestParking:
    def test_park_moves_the_tree_out_of_the_served_path(self, media_root):
        _uploads(media_root, "acme")

        assert park_tenant_media("acme") is True

        assert not (media_root / "acme").exists()
        assert (
            media_root / ".suspended" / "acme" / "uploads" / "logo.png"
        ).exists()

    def test_restore_moves_it_back(self, media_root):
        _uploads(media_root, "acme")
        park_tenant_media("acme")

        assert restore_tenant_media("acme") is True

        assert (
            media_root / "acme" / "uploads" / "logo.png"
        ).read_text() == "img"
        assert not (media_root / ".suspended" / "acme").exists()

    def test_nothing_to_move_is_not_an_error(self, media_root):
        assert park_tenant_media("acme") is False
        assert restore_tenant_media("acme") is False

    def test_neither_direction_merges_two_trees(self, media_root, caplog):
        _uploads(media_root, "acme")
        park_tenant_media("acme")
        # Files uploaded while suspended recreated the served tree.
        (media_root / "acme").mkdir()
        (media_root / "acme" / "new.png").write_text("new")

        assert restore_tenant_media("acme") is False

        assert (media_root / "acme" / "new.png").exists()
        assert (
            media_root / ".suspended" / "acme" / "uploads" / "logo.png"
        ).exists()
        assert "already exists" in caplog.text

    def test_other_stores_are_untouched(self, media_root):
        _uploads(media_root, "acme")
        _uploads(media_root, "other")

        park_tenant_media("acme")

        assert (media_root / "other" / "uploads" / "logo.png").exists()


class TestSuspendAndActivate:
    def test_suspend_parks_the_originals_before_the_flush_is_dispatched(
        self, media_root
    ):
        from tenant.lifecycle import suspend_tenant
        from tenant.models import SuspendedReason

        _uploads(media_root, "park_suspend")
        tenant = store_tenant("park_suspend")
        seen = {}

        def dispatched(schema, plan):
            seen["parked"] = (media_root / ".suspended" / schema).exists()

        with mock.patch(
            "tenant.tasks.flush_tenant_media_task.delay", side_effect=dispatched
        ):
            assert suspend_tenant(tenant, reason=SuspendedReason.MANUAL) is True

        assert seen == {"parked": True}
        assert not (media_root / "park_suspend").exists()

    def test_a_failed_park_still_suspends_and_flushes(self, media_root, caplog):
        from tenant.lifecycle import suspend_tenant
        from tenant.models import SuspendedReason

        _uploads(media_root, "park_fails")
        tenant = store_tenant("park_fails")

        with (
            mock.patch(
                "tenant.media_parking.os.rename", side_effect=OSError("EIO")
            ),
            mock.patch("tenant.tasks.flush_tenant_media_task.delay") as delay,
        ):
            assert suspend_tenant(tenant, reason=SuspendedReason.MANUAL) is True

        delay.assert_called_once()
        tenant.refresh_from_db()
        assert tenant.is_active is False
        assert "could not park the media of park_fails" in caplog.text

    def test_activate_restores_the_originals_and_flushes_the_placeholders(
        self, media_root
    ):
        from tenant.lifecycle import activate_tenant, suspend_tenant
        from tenant.models import SuspendedReason

        _uploads(media_root, "park_activate")
        tenant = store_tenant("park_activate")
        with mock.patch("tenant.tasks.flush_tenant_media_task.delay"):
            suspend_tenant(tenant, reason=SuspendedReason.MANUAL)

        with mock.patch("tenant.tasks.flush_tenant_media_task.delay") as delay:
            assert activate_tenant(tenant) is True

        assert (media_root / "park_activate" / "uploads" / "logo.png").exists()
        delay.assert_called_once()
        assert delay.call_args.args[0] == "park_activate"

    def test_activate_with_nothing_parked_dispatches_nothing(self, media_root):
        from tenant.lifecycle import activate_tenant

        tenant = store_tenant(
            "park_none",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=1),
        )

        with mock.patch("tenant.tasks.flush_tenant_media_task.delay") as delay:
            assert activate_tenant(tenant) is True

        delay.assert_not_called()


class TestDestroyPurgesTheOwnZone:
    @pytest.fixture(autouse=True)
    def _platform(self, settings):
        settings.CLOUDFLARE_PLATFORM_ZONE_NAME = "grooveshop.space"
        settings.CLOUDFLARE_PLATFORM_ZONE_ID = PLATFORM_ZONE
        settings.CLOUDFLARE_PLATFORM_API_TOKEN = "platform-token"
        settings.MEDIA_STREAM_BASE_URL = "https://assets.grooveshop.space"

    def test_the_own_zone_is_purged_after_the_row_is_gone(self, media_root):
        from core.cache import edge
        from tenant.lifecycle import destroy_tenant
        from tenant.models import Tenant, TenantDomain

        tenant = store_tenant(
            "own_destroy",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=25),
            cloudflare_zone_id=OWN_ZONE,
            cloudflare_api_token="own-token",
        )
        TenantDomain.objects.create(
            domain="assets.shop.example", tenant=tenant, is_primary=False
        )
        with (
            mock.patch("tenant.tasks.flush_tenant_media_task.delay") as delay,
            mock.patch.object(
                Tenant,
                "delete",
                autospec=True,
                side_effect=lambda t, **kw: (
                    type(t)
                    .objects.filter(pk=t.pk)
                    .update(cloudflare_zone_id="", cloudflare_api_token="")
                ),
            ),
            mock.patch("tenant.offboarding.purge_search_indexes"),
        ):
            destroy_tenant(tenant)
        schema, plan = delay.call_args.args

        # What the Celery task does with the plan, after the row is gone.
        with mock.patch.object(edge, "purge_prefixes") as purge:
            edge.purge_media(schema, plan)

        purged = {call.args[0] for call in purge.call_args_list}
        assert edge.Zone(OWN_ZONE, "own-token") in purged
        assert edge.Zone(PLATFORM_ZONE, "platform-token") in purged


class TestActivationNeverLeavesImagesParked:
    def test_a_failed_restore_keeps_the_store_suspended(self, media_root):
        from tenant.lifecycle import activate_tenant, suspend_tenant
        from tenant.models import SuspendedReason

        _uploads(media_root, "restore_fails")
        tenant = store_tenant("restore_fails")
        with mock.patch("tenant.tasks.flush_tenant_media_task.delay"):
            suspend_tenant(tenant, reason=SuspendedReason.MANUAL)

        with (
            mock.patch(
                "tenant.media_parking.os.rename", side_effect=OSError("EIO")
            ),
            mock.patch("tenant.tasks.flush_tenant_media_task.delay") as delay,
            pytest.raises(OSError),
        ):
            activate_tenant(tenant)

        tenant.refresh_from_db()
        assert tenant.is_active is False
        assert tenant.suspended_at is not None
        delay.assert_not_called()


class TestPurgeTokenFreshness:
    @pytest.fixture(autouse=True)
    def _platform(self, settings):
        settings.CLOUDFLARE_PLATFORM_ZONE_NAME = "grooveshop.space"
        settings.CLOUDFLARE_PLATFORM_ZONE_ID = PLATFORM_ZONE
        settings.CLOUDFLARE_PLATFORM_API_TOKEN = "platform-token"

    def test_a_token_rotated_after_planning_is_used_while_the_row_exists(self):
        from core.cache import edge
        from core.db.encrypted_fields import encrypt_value

        store_tenant(
            "rotated",
            cloudflare_zone_id=OWN_ZONE,
            cloudflare_api_token="new-token",
        )
        plan = [
            {
                "zone_id": OWN_ZONE,
                "prefixes": ["assets.shop.example/m/rotated/"],
                "token_ciphertext": encrypt_value("old-token"),
            }
        ]

        with mock.patch.object(edge, "purge_prefixes") as purge:
            edge.purge_media("rotated", plan)

        purge.assert_called_once_with(
            edge.Zone(OWN_ZONE, "new-token"), ["assets.shop.example/m/rotated/"]
        )


class TestDevMediaRouteMirrorsTheStaticServer:
    """The dev/CI media route must refuse what production refuses: a
    dot-segment path, which is where a suspended store's media is parked."""

    @pytest.fixture
    def served_media(self, settings):
        root = Path(settings.MEDIA_ROOT)
        live = root / "devroute" / "uploads"
        parked = root / ".suspended" / "devroute" / "uploads"
        for directory in (live, parked):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "logo.png").write_bytes(b"img")
        yield
        shutil.rmtree(root / "devroute", ignore_errors=True)
        shutil.rmtree(root / ".suspended" / "devroute", ignore_errors=True)

    @pytest.mark.usefixtures("served_media")
    @pytest.mark.parametrize(
        "url",
        [
            "/media/.suspended/devroute/uploads/logo.png",
            "/media/%2Esuspended/devroute/uploads/logo.png",
            "/media/devroute/../.suspended/devroute/uploads/logo.png",
        ],
    )
    def test_a_parked_file_is_not_served(self, client, url):
        assert client.get(url).status_code == 404

    @pytest.mark.usefixtures("served_media")
    def test_a_live_file_is_served(self, client):
        response = client.get("/media/devroute/uploads/logo.png")
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == b"img"
