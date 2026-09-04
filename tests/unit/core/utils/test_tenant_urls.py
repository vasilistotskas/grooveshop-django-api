"""Tests for ``core.utils.tenant_urls``.

The helpers read ``connection.tenant`` (set by
``django_tenants.TenantMainMiddleware`` in production and by
``TenantTask.__call__`` in Celery). Callers use them in place of
``settings.NUXT_BASE_URL`` so outbound emails / push notifications /
WebSocket link-backs resolve to the requesting tenant's domain.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.db import connection
from django.test import override_settings

from core.utils.tenant_urls import (
    get_tenant_api_base_url,
    get_tenant_assets_base_url,
    get_tenant_base_url,
    get_tenant_frontend_url,
    get_tenant_static_base_url,
)


@pytest.fixture
def bind_tenant(monkeypatch):
    def _bind(t):
        monkeypatch.setattr(connection, "tenant", t, raising=False)
        # Pin `schema_name` at its current value so monkeypatch restores
        # it at teardown. `schema_context.__exit__` calls
        # `set_tenant(previous)`, a real mutation of the shared
        # connection that monkeypatch does not otherwise track — without
        # this the worker is left outside the public schema and the next
        # test to create a Tenant fails somewhere unrelated.
        monkeypatch.setattr(
            connection, "schema_name", connection.schema_name, raising=False
        )

    yield _bind


def _fake_tenant(primary_domain: str, schema_name: str = "tenant_a"):
    """Build the minimum tenant shape the helpers touch."""
    primary_domain_obj = SimpleNamespace(domain=primary_domain)
    domains_qs = MagicMock()
    domains_qs.filter.return_value.first.return_value = primary_domain_obj
    return SimpleNamespace(schema_name=schema_name, domains=domains_qs)


def _fake_tenant_with_rows(
    rows: list[tuple[str, bool]], schema_name="tenant_a"
):
    """Build a tenant whose ``.domains`` manager answers the filter
    shapes ``get_tenant_api_base_url`` issues — ``domain__istartswith``
    and ``is_primary=True`` — backed by a real (small) list instead of a
    single canned MagicMock return value. The prefix lookup iterates its
    result, so the ordered queryset is iterable too.

    ``rows`` is a list of ``(domain, is_primary)`` tuples.
    """
    domain_objs = [SimpleNamespace(domain=d, is_primary=p) for d, p in rows]

    def _filter(**kwargs):
        results = list(domain_objs)
        if "domain__istartswith" in kwargs:
            prefix = kwargs["domain__istartswith"].lower()
            results = [
                r for r in results if r.domain.lower().startswith(prefix)
            ]
        if "is_primary" in kwargs:
            results = [
                r for r in results if r.is_primary == kwargs["is_primary"]
            ]

        filtered = MagicMock()
        filtered.first.return_value = results[0] if results else None
        ordered = MagicMock()
        # Real query orders by -is_primary; mirror that so a primary
        # "api" row wins over a non-primary one when both match.
        ordered_results = sorted(
            results, key=lambda r: r.is_primary, reverse=True
        )
        ordered.first.return_value = (
            ordered_results[0] if ordered_results else None
        )
        # The prefix lookup ITERATES the ordered queryset (it needs every
        # candidate so it can require a separator after the prefix — see
        # ``_has_prefix_boundary``), so the double has to be iterable and
        # not just answer .first().
        ordered.__iter__ = lambda _self: iter(ordered_results)
        filtered.order_by.return_value = ordered
        return filtered

    domains_qs = MagicMock()
    domains_qs.filter.side_effect = _filter
    return SimpleNamespace(schema_name=schema_name, domains=domains_qs)


class TestGetTenantBaseUrl:
    def test_uses_tenant_primary_domain(self, bind_tenant):
        bind_tenant(_fake_tenant(primary_domain="tenant-b.com"))
        assert get_tenant_base_url() == "https://tenant-b.com"

    @override_settings(NUXT_BASE_URL="https://fallback.example")
    def test_fallback_to_settings_when_no_tenant(self, bind_tenant):
        bind_tenant(None)
        assert get_tenant_base_url() == "https://fallback.example"

    @override_settings(NUXT_BASE_URL="https://fallback.example/")
    def test_fallback_strips_trailing_slash(self, bind_tenant):
        bind_tenant(None)
        # Trailing slash on NUXT_BASE_URL is idempotent — rstrip in the
        # helper prevents `//path` when the caller concatenates.
        assert get_tenant_base_url() == "https://fallback.example"

    def test_fallback_to_settings_when_no_primary_domain(self, bind_tenant):
        tenant = SimpleNamespace(schema_name="tenant_a")
        domains_qs = MagicMock()
        domains_qs.filter.return_value.first.return_value = None
        tenant.domains = domains_qs
        bind_tenant(tenant)

        with override_settings(NUXT_BASE_URL="https://fallback.example"):
            assert get_tenant_base_url() == "https://fallback.example"


class TestGetTenantFrontendUrl:
    def test_prepends_leading_slash_when_missing(self, bind_tenant):
        bind_tenant(_fake_tenant(primary_domain="webside.gr"))
        assert (
            get_tenant_frontend_url("account/orders/42")
            == "https://webside.gr/account/orders/42"
        )

    def test_respects_leading_slash_when_present(self, bind_tenant):
        bind_tenant(_fake_tenant(primary_domain="webside.gr"))
        assert (
            get_tenant_frontend_url("/account/orders/42")
            == "https://webside.gr/account/orders/42"
        )

    def test_empty_path_returns_base_url(self, bind_tenant):
        bind_tenant(_fake_tenant(primary_domain="webside.gr"))
        assert get_tenant_frontend_url("") == "https://webside.gr"

    def test_switches_tenants_per_call(self, bind_tenant):
        bind_tenant(_fake_tenant(primary_domain="tenant-a.example"))
        first = get_tenant_frontend_url("/cart")
        assert first == "https://tenant-a.example/cart"

        bind_tenant(_fake_tenant(primary_domain="tenant-b.example"))
        second = get_tenant_frontend_url("/cart")
        assert second == "https://tenant-b.example/cart"


class TestGetTenantApiBaseUrl:
    """Unlike ``get_tenant_base_url`` (storefront), this resolves the
    tenant's API origin for links with no Nuxt proxy in front of them
    (e.g. newsletter unsubscribe / subscription confirmation)."""

    def test_prefers_explicit_api_domain_row(self, bind_tenant):
        bind_tenant(
            _fake_tenant_with_rows(
                [
                    ("webside.gr", True),
                    ("api.webside.gr", False),
                ]
            )
        )
        assert get_tenant_api_base_url() == "https://api.webside.gr"

    def test_matches_non_dotted_api_prefix(self, bind_tenant):
        # Staging convention: "api-staging.webside.gr" — starts with
        # "api" but not "api.".
        bind_tenant(
            _fake_tenant_with_rows(
                [
                    ("webside.gr", True),
                    ("api-staging.webside.gr", False),
                ]
            )
        )
        assert get_tenant_api_base_url() == "https://api-staging.webside.gr"

    def test_falls_back_to_derived_api_subdomain(self, bind_tenant):
        # No explicit "api*" row at all — derive from the primary domain.
        bind_tenant(_fake_tenant_with_rows([("tenant-b.com", True)]))
        assert get_tenant_api_base_url() == "https://api.tenant-b.com"

    @override_settings(API_BASE_URL="https://fallback-api.example")
    def test_falls_back_to_settings_when_no_tenant(self, bind_tenant):
        bind_tenant(None)
        assert get_tenant_api_base_url() == "https://fallback-api.example"

    @override_settings(API_BASE_URL="https://fallback-api.example/")
    def test_fallback_strips_trailing_slash(self, bind_tenant):
        bind_tenant(None)
        assert get_tenant_api_base_url() == "https://fallback-api.example"

    @override_settings(API_BASE_URL="https://fallback-api.example")
    def test_falls_back_when_no_domains_at_all(self, bind_tenant):
        bind_tenant(_fake_tenant_with_rows([]))
        assert get_tenant_api_base_url() == "https://fallback-api.example"

    def test_switches_tenants_per_call(self, bind_tenant):
        bind_tenant(_fake_tenant_with_rows([("tenant-a.example", True)]))
        first = get_tenant_api_base_url()
        assert first == "https://api.tenant-a.example"

        bind_tenant(
            _fake_tenant_with_rows(
                [
                    ("tenant-b.example", True),
                    ("api.tenant-b.example", False),
                ]
            )
        )
        second = get_tenant_api_base_url()
        assert second == "https://api.tenant-b.example"


class TestGetTenantAssetsBaseUrl:
    """Media/image-processing origin — mirrors ``get_tenant_api_base_url``
    with the ``assets.`` prefix. Unlike the merchant CREDENTIAL helpers,
    this one DOES fall back to ``settings.MEDIA_STREAM_BASE_URL`` for
    the no-tenant case — it's platform infrastructure, not a secret."""

    def test_prefers_explicit_assets_domain_row(self, bind_tenant):
        bind_tenant(
            _fake_tenant_with_rows(
                [
                    ("webside.gr", True),
                    ("assets.webside.gr", False),
                ]
            )
        )
        assert get_tenant_assets_base_url() == "https://assets.webside.gr"

    @override_settings(MEDIA_STREAM_BASE_URL="https://platform-media.example")
    def test_no_explicit_row_uses_platform_origin(self, bind_tenant):
        # Asset hosts do NOT derive from the primary domain: the media
        # service is platform infrastructure (tenancy enforced at the
        # path level), so a tenant without a dedicated assets* row
        # shares the platform origin. A derived assets.<primary> host
        # pointed at DNS that need not exist (observed live on staging).
        bind_tenant(_fake_tenant_with_rows([("tenant-b.com", True)]))
        assert get_tenant_assets_base_url() == "https://platform-media.example"

    @override_settings(MEDIA_STREAM_BASE_URL="https://fallback-media.example")
    def test_falls_back_to_settings_when_no_tenant(self, bind_tenant):
        bind_tenant(None)
        assert get_tenant_assets_base_url() == "https://fallback-media.example"

    @override_settings(MEDIA_STREAM_BASE_URL="https://fallback-media.example/")
    def test_fallback_strips_trailing_slash(self, bind_tenant):
        bind_tenant(None)
        assert get_tenant_assets_base_url() == "https://fallback-media.example"

    @override_settings(MEDIA_STREAM_BASE_URL="https://fallback-media.example")
    def test_falls_back_when_no_domains_at_all(self, bind_tenant):
        bind_tenant(_fake_tenant_with_rows([]))
        assert get_tenant_assets_base_url() == "https://fallback-media.example"


class TestGetTenantStaticBaseUrl:
    """Static-file origin — mirrors ``get_tenant_api_base_url`` with the
    ``static.`` prefix. Falls back to ``settings.STATIC_BASE_URL`` for
    the no-tenant case (platform infrastructure, not a secret)."""

    def test_prefers_explicit_static_domain_row(self, bind_tenant):
        bind_tenant(
            _fake_tenant_with_rows(
                [
                    ("webside.gr", True),
                    ("static.webside.gr", False),
                ]
            )
        )
        assert get_tenant_static_base_url() == "https://static.webside.gr"

    @override_settings(STATIC_BASE_URL="https://platform-static.example")
    def test_no_explicit_row_uses_platform_origin(self, bind_tenant):
        bind_tenant(_fake_tenant_with_rows([("tenant-b.com", True)]))
        assert get_tenant_static_base_url() == "https://platform-static.example"

    @override_settings(STATIC_BASE_URL="https://fallback-static.example")
    def test_falls_back_to_settings_when_no_tenant(self, bind_tenant):
        bind_tenant(None)
        assert get_tenant_static_base_url() == "https://fallback-static.example"

    @override_settings(STATIC_BASE_URL="https://fallback-static.example/")
    def test_fallback_strips_trailing_slash(self, bind_tenant):
        bind_tenant(None)
        assert get_tenant_static_base_url() == "https://fallback-static.example"

    @override_settings(STATIC_BASE_URL="https://fallback-static.example")
    def test_falls_back_when_no_domains_at_all(self, bind_tenant):
        bind_tenant(_fake_tenant_with_rows([]))
        assert get_tenant_static_base_url() == "https://fallback-static.example"


class TestPrefixRequiresASeparator:
    """``istartswith`` alone matched any domain beginning with those
    letters, so a tenant whose own storefront is ``apiary.gr`` resolved
    that domain as its API host — and every link, WebSocket URL and CSP
    entry built from it pointed at the storefront instead."""

    def test_bare_prefix_match_is_not_treated_as_the_service_host(
        self, bind_tenant
    ):
        bind_tenant(
            _fake_tenant_with_rows(
                [
                    ("apiary.gr", True),
                ]
            )
        )
        # Falls through to the derived form rather than claiming
        # apiary.gr is the API host.
        assert get_tenant_api_base_url() == "https://api.apiary.gr"

    def test_static_prefix_needs_a_separator_too(self, bind_tenant):
        bind_tenant(
            _fake_tenant_with_rows(
                [
                    ("staticshop.com", True),
                ]
            )
        )
        # No explicit static* row, and assets/static do NOT derive —
        # a white-label origin is an opt-in, so this is the platform one.
        assert get_tenant_static_base_url() != "https://staticshop.com"

    def test_both_real_separators_still_match(self, bind_tenant):
        # Production uses a dot, staging uses a dash.
        for host in ("api.example.gr", "api-staging.example.gr"):
            bind_tenant(
                _fake_tenant_with_rows([("example.gr", True), (host, False)])
            )
            assert get_tenant_api_base_url() == f"https://{host}"
