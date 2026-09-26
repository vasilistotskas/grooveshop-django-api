"""Purging storefront pages from the Cloudflare edge cache.

A store's pages must be purged from every zone that holds them, with the
credentials of whoever owns that zone: the store's own account for its own
domain, the platform's for a platform hostname. The tag names are a
contract with the storefront (``server/utils/edgeCache.ts``).
"""

from __future__ import annotations

from unittest import mock

import pytest
import requests
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError

from core.cache import edge
from core.cache.service import CacheService

pytestmark = pytest.mark.django_db

PLATFORM_ZONE = "a" * 32
OWN_ZONE = "b" * 32


@pytest.fixture(autouse=True)
def _platform(settings):
    settings.CLOUDFLARE_PLATFORM_ZONE_NAME = "grooveshop.space"
    settings.CLOUDFLARE_PLATFORM_ZONE_ID = PLATFORM_ZONE
    settings.CLOUDFLARE_PLATFORM_API_TOKEN = "platform-token"


def _tenant(schema, domains, **credentials):
    from tenant.models import Tenant, TenantDomain

    tenant = Tenant(
        schema_name=schema,
        name=schema,
        slug=schema.replace("_", "-"),
        owner_email=f"owner@{schema}.example",
        **credentials,
    )
    tenant.auto_create_schema = False
    tenant.save()
    for index, domain in enumerate(domains):
        TenantDomain.objects.create(
            domain=domain, tenant=tenant, is_primary=index == 0
        )
    return tenant


def _ok(*_args, **_kwargs):
    response = mock.Mock(status_code=200)
    response.json.return_value = {"success": True, "errors": []}
    return response


class TestTenantZones:
    def test_platform_hostname_uses_the_platform_zone(self):
        tenant = _tenant("edge_demo", ["demo.grooveshop.space"])
        assert edge.tenant_zones(tenant) == [
            edge.Zone(PLATFORM_ZONE, "platform-token")
        ]

    def test_own_domain_uses_the_stores_own_zone_only(self):
        tenant = _tenant(
            "edge_own",
            ["shop.example", "www.shop.example"],
            cloudflare_zone_id=OWN_ZONE,
            cloudflare_api_token="own-token",
        )
        assert edge.tenant_zones(tenant) == [edge.Zone(OWN_ZONE, "own-token")]

    def test_a_store_on_both_is_purged_in_both(self):
        tenant = _tenant(
            "edge_both",
            ["shop.example", "shop.grooveshop.space"],
            cloudflare_zone_id=OWN_ZONE,
            cloudflare_api_token="own-token",
        )
        assert [z.zone_id for z in edge.tenant_zones(tenant)] == [
            OWN_ZONE,
            PLATFORM_ZONE,
        ]

    def test_a_lookalike_domain_is_not_on_the_platform_zone(self):
        tenant = _tenant("edge_lookalike", ["evilgrooveshop.space"])
        assert edge.tenant_zones(tenant) == []

    def test_no_platform_credentials_means_no_platform_zone(self, settings):
        settings.CLOUDFLARE_PLATFORM_API_TOKEN = ""
        tenant = _tenant("edge_unset", ["demo.grooveshop.space"])
        assert edge.tenant_zones(tenant) == []


class TestPurgeTags:
    def test_sends_the_tags_with_the_zones_own_token(self):
        with mock.patch.object(edge.requests, "post", side_effect=_ok) as post:
            edge.purge_tags(edge.Zone(OWN_ZONE, "own-token"), ["t1"])
        url = post.call_args.args[0]
        assert url.endswith(f"/zones/{OWN_ZONE}/purge_cache")
        assert post.call_args.kwargs["json"] == {"tags": ["t1"]}
        assert (
            post.call_args.kwargs["headers"]["Authorization"]
            == "Bearer own-token"
        )

    def test_a_refusal_raises(self):
        refused = mock.Mock(status_code=429, text="rate limited")
        refused.json.return_value = {"success": False, "errors": ["limit"]}
        with (
            mock.patch.object(edge.requests, "post", return_value=refused),
            pytest.raises(edge.EdgePurgeError, match="429"),
        ):
            edge.purge_tags(edge.Zone(OWN_ZONE, "t"), ["t1"])

    def test_an_unreachable_api_raises(self):
        with (
            mock.patch.object(
                edge.requests,
                "post",
                side_effect=requests.ConnectionError("down"),
            ),
            pytest.raises(edge.EdgePurgeError, match="unreachable"),
        ):
            edge.purge_tags(edge.Zone(OWN_ZONE, "t"), ["t1"])


class TestPurges:
    def test_a_tenant_purge_sends_the_tenant_tag(self):
        tenant = _tenant("edge_tag", ["tag.grooveshop.space"])
        with mock.patch.object(edge, "purge_tags") as purge:
            assert edge.purge_tenant(tenant) == 1
        purge.assert_called_once_with(
            edge.Zone(PLATFORM_ZONE, "platform-token"),
            ["storefront-html-edge_tag"],
        )

    def test_the_deploy_purge_covers_each_zone_once(self):
        _tenant("edge_p1", ["p1.grooveshop.space"])
        for schema in ("edge_o1", "edge_o2"):
            _tenant(
                schema,
                [f"{schema}.example"],
                cloudflare_zone_id=OWN_ZONE,
                cloudflare_api_token="own-token",
            )
        with mock.patch.object(edge, "purge_tags") as purge:
            assert edge.purge_all_storefronts() == 2
        assert {c.args[0].zone_id for c in purge.call_args_list} == {
            PLATFORM_ZONE,
            OWN_ZONE,
        }
        assert all(
            c.args[1] == ["storefront-html"] for c in purge.call_args_list
        )


class TestScheduling:
    def test_repeated_edits_queue_one_purge(self):
        with mock.patch(
            "core.tasks.purge_edge_cache_task.apply_async"
        ) as queue:
            assert edge.schedule_purge() is True
            assert edge.schedule_purge() is False
        queue.assert_called_once_with(countdown=edge.PURGE_DELAY_SECONDS)

    def test_the_purge_reopens_the_window_when_it_runs(self):
        with (
            mock.patch("core.tasks.purge_edge_cache_task.apply_async"),
            mock.patch.object(edge, "purge_all_storefronts", return_value=1),
        ):
            edge.schedule_purge()
            edge.run_scheduled_purge()
            assert edge.schedule_purge() is True

    def test_the_public_schema_purges_every_storefront(self):
        with mock.patch.object(
            edge, "purge_all_storefronts", return_value=3
        ) as purge_all:
            assert edge.run_scheduled_purge() == 3
        purge_all.assert_called_once()


class TestCacheServiceHook:
    def test_purging_rendered_pages_schedules_an_edge_purge(self):
        with (
            mock.patch.object(edge, "schedule_purge") as schedule,
            mock.patch("core.cache.nuxt.request_purge"),
        ):
            CacheService.purge(["products"], include_related=False)
        schedule.assert_called_once()

    def test_a_dry_run_purges_nothing_at_the_edge(self):
        with (
            mock.patch.object(edge, "schedule_purge") as schedule,
            mock.patch("core.cache.nuxt.request_purge"),
        ):
            CacheService.purge(
                ["products"], include_related=False, dry_run=True
            )
        schedule.assert_not_called()

    def test_api_data_alone_leaves_the_edge_alone(self):
        with (
            mock.patch.object(edge, "schedule_purge") as schedule,
            mock.patch("core.cache.nuxt.request_purge"),
        ):
            CacheService.purge(["pay_way"], include_related=False)
        schedule.assert_not_called()


class TestCredentials:
    def test_zone_and_token_must_be_set_together(self):
        from tenant.models import Tenant

        tenant = Tenant(schema_name="edge_half", cloudflare_zone_id=OWN_ZONE)
        with pytest.raises(ValidationError, match="together"):
            tenant._validate_cloudflare_credentials()

    def test_the_token_never_reaches_the_history_table(self):
        from tenant.models import Tenant

        history_fields = {
            f.name for f in Tenant.history.model._meta.get_fields()
        }
        assert "cloudflare_api_token" not in history_fields
        assert "cloudflare_zone_id" in history_fields


class TestCommand:
    def test_retries_then_reports(self):
        with (
            mock.patch.object(
                edge,
                "purge_all_storefronts",
                side_effect=[edge.EdgePurgeError("429"), 2],
            ),
            mock.patch("core.management.commands.purge_edge_cache.time.sleep"),
        ):
            call_command("purge_edge_cache")

    def test_fails_when_cloudflare_keeps_refusing(self):
        with (
            mock.patch.object(
                edge,
                "purge_all_storefronts",
                side_effect=edge.EdgePurgeError("429"),
            ),
            mock.patch("core.management.commands.purge_edge_cache.time.sleep"),
            pytest.raises(CommandError, match="429"),
        ):
            call_command("purge_edge_cache")
