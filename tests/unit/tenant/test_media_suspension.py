"""A suspended store's images are refused by state, not by moving its files.

Django tells media-stream which stores are suspended (the internal tenant
feed, ``tenant/internal.py``) and queues a media flush on every suspension
and reactivation, which makes media-stream re-read the feed at once and
drops the cached copies at media-stream and the edge. The originals stay in
``MEDIA_ROOT/{schema}``: only media-stream reads them, in-cluster, and the
static host redirects every public request for one to media-stream.
"""

from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path
from unittest import mock

import pytest
from django.utils import timezone

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


class TestSuspendAndActivate:
    def test_suspension_leaves_the_originals_in_place_and_flushes(
        self, media_root
    ):
        from tenant.lifecycle import suspend_tenant
        from tenant.models import SuspendedReason

        _uploads(media_root, "keep_files")
        tenant = store_tenant("keep_files")

        with mock.patch(
            "tenant.tasks.flush_tenant_media_task.apply_async"
        ) as dispatch:
            assert suspend_tenant(tenant, reason=SuspendedReason.MANUAL) is True

        assert (media_root / "keep_files" / "uploads" / "logo.png").exists()
        assert sorted(p.name for p in media_root.iterdir()) == ["keep_files"]
        dispatch.assert_called_once()
        assert dispatch.call_args.kwargs["args"][0] == "keep_files"

    def test_activation_flushes_so_media_stream_serves_the_store_again(
        self, media_root
    ):
        """The flush is what makes media-stream re-read the tenant feed and
        serve the store again at once, rather than at its next poll."""
        from tenant.lifecycle import activate_tenant

        tenant = store_tenant(
            "back_on",
            is_active=False,
            suspended_at=timezone.now() - timedelta(hours=1),
        )

        with mock.patch(
            "tenant.tasks.flush_tenant_media_task.apply_async"
        ) as dispatch:
            assert activate_tenant(tenant) is True

        dispatch.assert_called_once()
        assert dispatch.call_args.kwargs["args"][0] == "back_on"


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
            mock.patch(
                "tenant.tasks.flush_tenant_media_task.apply_async"
            ) as dispatch,
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
        schema, plan = dispatch.call_args.kwargs["args"]

        # What the Celery task does with the plan, after the row is gone.
        with mock.patch.object(edge, "purge_prefixes") as purge:
            edge.purge_media(schema, plan)

        purged = {call.args[0] for call in purge.call_args_list}
        assert edge.Zone(OWN_ZONE, "own-token") in purged
        assert edge.Zone(PLATFORM_ZONE, "platform-token") in purged


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
    dot-segment path (``location ~ /\\. { deny all; }``)."""

    @pytest.fixture
    def served_media(self, settings):
        root = Path(settings.MEDIA_ROOT)
        live = root / "devroute" / "uploads"
        hidden = root / ".hidden" / "devroute" / "uploads"
        for directory in (live, hidden):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "logo.png").write_bytes(b"img")
        yield
        shutil.rmtree(root / "devroute", ignore_errors=True)
        shutil.rmtree(root / ".hidden", ignore_errors=True)

    @pytest.mark.usefixtures("served_media")
    @pytest.mark.parametrize(
        "url",
        [
            "/media/.hidden/devroute/uploads/logo.png",
            "/media/%2Ehidden/devroute/uploads/logo.png",
            "/media/devroute/../.hidden/devroute/uploads/logo.png",
        ],
    )
    def test_a_dot_segment_path_is_not_served(self, client, url):
        assert client.get(url).status_code == 404

    @pytest.mark.usefixtures("served_media")
    def test_a_live_file_is_served(self, client):
        response = client.get("/media/devroute/uploads/logo.png")
        assert response.status_code == 200
        assert b"".join(response.streaming_content) == b"img"
