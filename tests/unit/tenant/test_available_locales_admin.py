"""``Tenant.available_locales`` in the platform's tenant form.

The field decides which language prefixes a store's storefront serves,
and it sat in no fieldset, so nobody could change it without a shell.
The platform edits it; a store operator sees it read-only (it is not in
``TENANT_SELF_EDITABLE_FIELDS``). Changing it purges what the storefront
cached for the store, including the sitemap.
"""

from __future__ import annotations

from unittest.mock import call, patch

import pytest
from django.conf import settings
from django.contrib.admin import site as admin_site
from django.test import RequestFactory

from core.cache.surfaces import SITEMAP_SEO_SURFACE, STORE_PAGES_SURFACE
from core.tasks import purge_store_locales_task
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tenant.models import (
    Tenant,
    TenantMembershipRole,
    UserTenantMembership,
)
from tests.utils.staff import store_tenant
from user.factories.account import UserAccountFactory

pytestmark = pytest.mark.django_db

DISPATCH = "tenant.switches.dispatch_on_commit"


def _request(user):
    request = RequestFactory().get("/admin/")
    request.user = user
    return request


def _superuser():
    user = UserAccountFactory(is_staff=True, is_superuser=True)
    setattr(user, PLATFORM_IDENTITY_ATTR, True)
    return user


def _operator(tenant):
    user = UserAccountFactory(is_staff=True)
    setattr(user, PLATFORM_IDENTITY_ATTR, True)
    UserTenantMembership.objects.create(
        user=user, tenant=tenant, role=TenantMembershipRole.OWNER
    )
    return user


def _listed_fields(model_admin, request, obj) -> set[str]:
    return {
        name
        for _title, options in model_admin.get_fieldsets(request, obj)
        for name in options["fields"]
    }


@pytest.fixture
def tenant():
    return store_tenant("locales_shop", default_locale="el")


@pytest.fixture
def model_admin():
    return admin_site._registry[Tenant]


class TestPlatformForm:
    def test_the_field_is_in_a_fieldset(self, model_admin, tenant):
        request = _request(_superuser())
        assert "available_locales" in _listed_fields(
            model_admin, request, tenant
        )

    def test_platform_staff_can_edit_it(self, model_admin, tenant):
        request = _request(_superuser())
        assert "available_locales" not in model_admin.get_readonly_fields(
            request, tenant
        )

    def test_a_store_operator_sees_it_read_only(
        self, model_admin, tenant, bind_tenant_and_schema
    ):
        bind_tenant_and_schema(tenant)
        request = _request(_operator(tenant))
        assert "available_locales" in model_admin.get_readonly_fields(
            request, tenant
        )

    def test_the_form_saves_a_supported_set(self, model_admin, tenant):
        request = _request(_superuser())
        form_class = model_admin.get_form(
            request, tenant, fields=["available_locales", "default_locale"]
        )
        form = form_class(
            {"available_locales": '["el", "en"]', "default_locale": "el"},
            instance=tenant,
        )
        assert form.is_valid(), form.errors
        form.save()
        tenant.refresh_from_db()
        assert tenant.available_locales == ["el", "en"]

    def test_the_form_refuses_a_locale_the_build_does_not_support(
        self, model_admin, tenant
    ):
        # ``fr`` is not a locale the storefront builds.
        request = _request(_superuser())
        form_class = model_admin.get_form(
            request, tenant, fields=["available_locales", "default_locale"]
        )
        form = form_class(
            {"available_locales": '["el", "fr"]', "default_locale": "el"},
            instance=tenant,
        )
        assert not form.is_valid()
        assert "available_locales" in form.errors


class TestPurgeOnChange:
    def test_a_change_purges_once_the_storefronts_copy_expired(self, tenant):
        with patch(DISPATCH) as dispatch:
            tenant.available_locales = ["el", "en"]
            tenant.save()

        assert dispatch.call_args_list == [
            call(
                purge_store_locales_task,
                schema_name="locales_shop",
                countdown=settings.STOREFRONT_TENANT_CACHE_SECONDS,
            )
        ]

    def test_saving_the_same_set_purges_nothing(self, tenant):
        with patch(DISPATCH) as dispatch:
            tenant.store_name = "Renamed"
            tenant.save()

        assert dispatch.call_args_list == []

    def test_the_task_purges_pages_and_sitemap(self):
        with patch("core.cache.service.CacheService.purge") as purge:
            purge_store_locales_task.run()

        purge.assert_called_once_with(
            [STORE_PAGES_SURFACE, SITEMAP_SEO_SURFACE],
            include_related=False,
        )
