"""Per-store AI profile: provider base URLs, validation, the resolve
payload, the write-only admin key, the platform API, and the move of the
old chat key into the profile."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.contrib.admin import site as admin_site
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client, RequestFactory
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from tenant.ai_providers import (
    AI_PROVIDER_BASE_URLS,
    serialize_ai_profile,
)
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tenant.cache import tenant_resolve_key
from tenant.enum import AIProvider, TenantMembershipRole
from tenant.models import Tenant, TenantDomain, UserTenantMembership
from tests.utils.staff import store_tenant
from user.factories.account import UserAccountFactory


def _request(user):
    request = RequestFactory().get(reverse("admin:index"))
    request.user = user
    return request


def _store_owner(tenant: Tenant):
    user = UserAccountFactory(is_staff=True)
    setattr(user, PLATFORM_IDENTITY_ATTR, True)
    UserTenantMembership.objects.create(
        user=user, tenant=tenant, role=TenantMembershipRole.OWNER
    )
    return user


def _superuser():
    user = UserAccountFactory(is_staff=True, is_superuser=True)
    setattr(user, PLATFORM_IDENTITY_ATTR, True)
    return user


class TestAIProviderBaseURLs:
    def test_every_provider_has_an_https_base_url(self):
        assert set(AI_PROVIDER_BASE_URLS) == set(AIProvider)
        for base_url in AI_PROVIDER_BASE_URLS.values():
            assert base_url.startswith("https://")
            assert base_url.endswith("/")


@pytest.mark.django_db
class TestAIProfileValidation:
    def test_empty_profile_is_valid(self):
        tenant = store_tenant("ai_empty")
        tenant.clean()
        assert serialize_ai_profile(tenant) is None

    def test_complete_profile_is_valid(self):
        tenant = store_tenant(
            "ai_complete",
            ai_provider=AIProvider.OPENROUTER,
            ai_model="anthropic/claude-sonnet-4:beta",
            ai_api_key="sk-or-v1-secret-token",
        )
        tenant.clean()
        profile = serialize_ai_profile(tenant)
        assert profile == {
            "provider": "openrouter",
            "base_url": "https://openrouter.ai/api/v1/",
            "model": "anthropic/claude-sonnet-4:beta",
            "api_key": "sk-or-v1-secret-token",
        }

    @pytest.mark.parametrize(
        ("provider", "model", "api_key", "missing_fields"),
        [
            (AIProvider.OPENAI, "", "", {"ai_model", "ai_api_key"}),
            ("", "gpt-4.1-mini", "", {"ai_provider", "ai_api_key"}),
            ("", "", "sk-only-key", {"ai_provider", "ai_model"}),
            (AIProvider.OPENAI, "gpt-4.1-mini", "", {"ai_api_key"}),
            (AIProvider.OPENAI, "", "sk-key", {"ai_model"}),
            ("", "gpt-4.1-mini", "sk-key", {"ai_provider"}),
        ],
    )
    def test_partial_profile_is_refused(
        self, provider, model, api_key, missing_fields
    ):
        tenant = store_tenant("ai_partial")
        tenant.ai_provider = provider
        tenant.ai_model = model
        tenant.ai_api_key = api_key
        with pytest.raises(ValidationError) as exc:
            tenant.clean()
        assert missing_fields <= set(exc.value.message_dict)

    def test_unknown_provider_is_refused(self):
        tenant = store_tenant("ai_bad_provider")
        tenant.ai_provider = "https://169.254.169.254/"
        tenant.ai_model = "gpt-4.1-mini"
        tenant.ai_api_key = "sk-key"
        with pytest.raises(ValidationError) as exc:
            tenant.full_clean()
        assert "ai_provider" in exc.value.message_dict

    @pytest.mark.parametrize(
        "bad_model",
        [
            "gpt 4.1",
            "https://evil.test/model",
            "vendor//model",
            "../secret",
            "model?foo=bar",
            "-leading-dash",
            "model\ninjected",
        ],
    )
    def test_malformed_model_is_refused(self, bad_model):
        tenant = store_tenant("ai_bad_model")
        tenant.ai_provider = AIProvider.OPENAI
        tenant.ai_model = bad_model
        tenant.ai_api_key = "sk-key"
        with pytest.raises(ValidationError) as exc:
            tenant.clean()
        assert "ai_model" in exc.value.message_dict

    @pytest.mark.parametrize(
        "bad_key",
        [" sk-padded", "sk-padded ", "Bearer sk-key", "sk-\nkey", "sk-ελλ"],
    )
    def test_malformed_api_key_is_refused(self, bad_key):
        tenant = store_tenant("ai_bad_key")
        tenant.ai_provider = AIProvider.OPENAI
        tenant.ai_model = "gpt-4.1-mini"
        tenant.ai_api_key = bad_key
        with pytest.raises(ValidationError) as exc:
            tenant.clean()
        assert "ai_api_key" in exc.value.message_dict


@pytest.mark.django_db
class TestTenantResolveAIProfile:
    def test_ai_profile_is_gateway_only_and_never_cached(self, settings):
        settings.AGENT_GATEWAY_INTERNAL_SECRET = "gw-secret"
        tenant = store_tenant(
            "ai_resolve_openai",
            ai_provider=AIProvider.OPENAI,
            ai_model="gpt-4.1-mini",
            ai_api_key="sk-openai-resolve-secret",
        )
        TenantDomain.objects.create(
            tenant=tenant, domain="ai-openai.example", is_primary=True
        )
        client = Client()
        url = f"{reverse('tenant:tenant-resolve')}?domain=ai-openai.example"

        public = client.get(url)
        assert public.status_code == status.HTTP_200_OK
        assert "aiProfile" not in public.json()
        assert "sk-openai-resolve-secret" not in public.content.decode()

        forged = client.get(url, HTTP_X_INTERNAL_TOKEN="wrong")
        assert "aiProfile" not in forged.json()

        gateway = client.get(url, HTTP_X_INTERNAL_TOKEN="gw-secret")
        assert gateway.status_code == status.HTTP_200_OK
        assert gateway["Cache-Control"] == "private, no-store"
        assert "X-Internal-Token" in gateway["Vary"]
        payload = gateway.json()
        assert payload["aiProfile"] == {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1/",
            "model": "gpt-4.1-mini",
            "apiKey": "sk-openai-resolve-secret",
        }
        assert "chatApiKey" not in payload

        # Public cache entry must stay free of the AI profile.
        tenant.refresh_from_db()
        cached = cache.get(tenant_resolve_key("ai-openai.example", tenant))
        assert cached is not None
        assert "ai_profile" not in cached
        assert "ai_api_key" not in cached

        after = client.get(url)
        assert "aiProfile" not in after.json()

    def test_store_without_profile_gets_null_ai_profile(self, settings):
        settings.AGENT_GATEWAY_INTERNAL_SECRET = "gw-secret"
        tenant = store_tenant("ai_resolve_none")
        TenantDomain.objects.create(
            tenant=tenant, domain="ai-none.example", is_primary=True
        )
        response = Client().get(
            f"{reverse('tenant:tenant-resolve')}?domain=ai-none.example",
            HTTP_X_INTERNAL_TOKEN="gw-secret",
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["aiProfile"] is None


def _ai_tab_fields(admin_, request, obj):
    return [
        field
        for _name, options in admin_.get_fieldsets(request, obj)
        for field in options["fields"]
    ]


@pytest.mark.django_db
class TestTenantAdminWriteOnlyAIKey:
    def _admin(self):
        return admin_site._registry[Tenant]

    def _form(self, request, tenant, data=None):
        form_class = self._admin().get_form(
            request,
            tenant,
            fields=["store_name", "ai_provider", "ai_model", "ai_api_key"],
        )
        if data is None:
            return form_class(instance=tenant)
        return form_class(
            {"store_name": tenant.store_name or "Store", **data},
            instance=tenant,
        )

    def test_store_owner_edits_the_profile(self, bind_tenant_and_schema):
        tenant = store_tenant("ai_admin_scope")
        owner = _store_owner(tenant)
        bind_tenant_and_schema(tenant)
        request = _request(owner)

        readonly = set(self._admin().get_readonly_fields(request, tenant))
        assert {"ai_provider", "ai_model", "ai_api_key"}.isdisjoint(readonly)
        listed = _ai_tab_fields(self._admin(), request, tenant)
        assert {"ai_provider", "ai_model", "ai_api_key"} <= set(listed)
        assert "ai_api_key_is_set" not in listed

    def test_view_only_page_shows_whether_a_key_is_set(
        self, bind_tenant_and_schema
    ):
        tenant = store_tenant(
            "ai_admin_viewonly",
            ai_provider=AIProvider.GEMINI,
            ai_model="gemini-3.5-flash-lite",
            ai_api_key="sk-viewonly-secret",
        )
        owner = _store_owner(tenant)
        bind_tenant_and_schema(tenant)
        with patch.object(
            self._admin(), "has_change_permission", return_value=False
        ):
            listed = _ai_tab_fields(self._admin(), _request(owner), tenant)
        assert "ai_api_key" not in listed
        assert "ai_api_key_is_set" in listed
        assert self._admin().ai_api_key_is_set(tenant) is True

    def test_blank_input_keeps_the_saved_key(self, bind_tenant_and_schema):
        secret = "sk-never-render-this-plaintext-ai-key"
        tenant = store_tenant(
            "ai_admin_keep",
            ai_provider=AIProvider.OPENAI,
            ai_model="gpt-4.1-mini",
            ai_api_key=secret,
        )
        bind_tenant_and_schema(None)
        request = _request(_superuser())

        rendered = self._form(request, tenant).as_p()
        assert 'type="password"' in rendered
        assert secret not in rendered

        form = self._form(
            request,
            tenant,
            {
                "store_name": "Updated Store",
                "ai_provider": AIProvider.OPENAI,
                "ai_model": "gpt-4.1",
                "ai_api_key": "",
            },
        )
        assert form.is_valid(), form.errors
        form.save()
        tenant.refresh_from_db()
        assert tenant.store_name == "Updated Store"
        assert tenant.ai_model == "gpt-4.1"
        assert tenant.ai_api_key == secret

    def test_a_new_key_replaces_the_saved_one_encrypted(
        self, bind_tenant_and_schema
    ):
        tenant = store_tenant(
            "ai_admin_rotate",
            ai_provider=AIProvider.GEMINI,
            ai_model="gemini-3.5-flash-lite",
            ai_api_key="old-key",
        )
        bind_tenant_and_schema(None)
        form = self._form(
            _request(_superuser()),
            tenant,
            {
                "ai_provider": AIProvider.GEMINI,
                "ai_model": "gemini-3.5-flash-lite",
                "ai_api_key": "new-key",
            },
        )
        assert form.is_valid(), form.errors
        form.save()
        tenant.refresh_from_db()
        assert tenant.ai_api_key == "new-key"
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT ai_api_key FROM tenant_tenant WHERE id = %s",
                [tenant.pk],
            )
            stored = cursor.fetchone()[0]
        assert stored
        assert "new-key" not in stored

    def test_clearing_provider_and_model_removes_the_key(
        self, bind_tenant_and_schema
    ):
        tenant = store_tenant(
            "ai_admin_off",
            ai_provider=AIProvider.MISTRAL,
            ai_model="mistral-small-latest",
            ai_api_key="mis-key",
        )
        bind_tenant_and_schema(None)
        form = self._form(
            _request(_superuser()),
            tenant,
            {"ai_provider": "", "ai_model": "", "ai_api_key": ""},
        )
        assert form.is_valid(), form.errors
        form.save()
        tenant.refresh_from_db()
        assert (tenant.ai_provider, tenant.ai_model, tenant.ai_api_key) == (
            "",
            "",
            "",
        )

    def test_a_provider_change_needs_a_new_key(self, bind_tenant_and_schema):
        tenant = store_tenant(
            "ai_admin_switch",
            ai_provider=AIProvider.GEMINI,
            ai_model="gemini-3.5-flash-lite",
            ai_api_key="gem-key",
        )
        bind_tenant_and_schema(None)
        request = _request(_superuser())

        kept_key = self._form(
            request,
            tenant,
            {
                "ai_provider": AIProvider.OPENAI,
                "ai_model": "gpt-4.1-mini",
                "ai_api_key": "",
            },
        )
        assert not kept_key.is_valid()
        assert "ai_api_key" in kept_key.errors

        new_key = self._form(
            request,
            tenant,
            {
                "ai_provider": AIProvider.OPENAI,
                "ai_model": "gpt-4.1-mini",
                "ai_api_key": "sk-openai",
            },
        )
        assert new_key.is_valid(), new_key.errors
        new_key.save()
        tenant.refresh_from_db()
        assert tenant.ai_provider == AIProvider.OPENAI
        assert tenant.ai_api_key == "sk-openai"

    def test_a_key_saved_after_the_form_loaded_is_not_kept_under_a_new_provider(
        self, bind_tenant_and_schema
    ):
        tenant = store_tenant(
            "ai_admin_stale",
            ai_provider=AIProvider.GEMINI,
            ai_model="gemini-3.5-flash-lite",
            ai_api_key="gem-key-1",
        )
        bind_tenant_and_schema(None)
        loaded = Tenant.objects.get(pk=tenant.pk)
        form = self._form(
            _request(_superuser()),
            loaded,
            {
                "ai_provider": AIProvider.OPENAI,
                "ai_model": "gpt-4.1-mini",
                "ai_api_key": "",
            },
        )
        # Another save replaces the Gemini key after this form loaded.
        Tenant.objects.filter(pk=tenant.pk).update(ai_api_key="gem-key-2")
        assert not form.is_valid()
        assert "ai_api_key" in form.errors

    def test_a_key_without_provider_and_model_is_refused(
        self, bind_tenant_and_schema
    ):
        tenant = store_tenant("ai_admin_partial")
        bind_tenant_and_schema(None)
        form = self._form(
            _request(_superuser()),
            tenant,
            {"ai_provider": "", "ai_model": "", "ai_api_key": "lonely-key"},
        )
        assert not form.is_valid()
        assert {"ai_provider", "ai_model"} <= set(form.errors)

    def test_change_view_never_renders_the_saved_key(
        self, bind_tenant_and_schema
    ):
        secret = "sk-html-secret-must-never-appear"
        tenant = store_tenant(
            "ai_admin_html",
            ai_provider=AIProvider.GEMINI,
            ai_model="gemini-3.5-flash-lite",
            ai_api_key=secret,
        )
        owner = _store_owner(tenant)
        superuser = _superuser()
        bind_tenant_and_schema(tenant)

        for actor in (owner, superuser):
            client = Client()
            client.force_login(
                actor, backend="tenant.auth_backends.PlatformStaffBackend"
            )
            response = client.get(
                reverse("admin:tenant_tenant_change", args=[tenant.pk])
            )
            assert response.status_code == status.HTTP_200_OK
            html = response.content.decode()
            assert secret not in html
            assert 'name="ai_api_key"' in html
            assert 'type="password"' in html


@pytest.mark.django_db
@pytest.mark.urls("tenant.urls_public")
class TestTenantAdminSerializerAIProfile:
    @pytest.fixture
    def operator_client(self):
        client = APIClient()
        client.force_authenticate(
            user=UserAccountFactory(is_staff=True, is_superuser=True)
        )
        with patch(
            "tenant.auth_backends.is_platform_staff_session", return_value=True
        ):
            yield client

    def test_patch_sets_profile_write_only_and_runs_the_store_rules(
        self, operator_client
    ):
        tenant = store_tenant("ai_api_patch")
        url = reverse("tenant-admin-detail", args=[tenant.pk])

        partial = operator_client.patch(
            url,
            {"aiProvider": "openai", "aiModel": "gpt-4.1-mini"},
            format="json",
        )
        assert partial.status_code == status.HTTP_400_BAD_REQUEST
        assert "ai_api_key" in partial.data

        ok = operator_client.patch(
            url,
            {
                "aiProvider": "openai",
                "aiModel": "gpt-4.1-mini",
                "aiApiKey": "sk-api-write-only",
            },
            format="json",
        )
        assert ok.status_code == status.HTTP_200_OK
        assert ok.data["ai_api_key_set"] is True
        assert "ai_api_key" not in ok.data

        read = operator_client.get(url)
        assert read.data["ai_api_key_set"] is True
        assert "sk-api-write-only" not in read.content.decode()

        switched = operator_client.patch(
            url, {"aiProvider": "mistral"}, format="json"
        )
        assert switched.status_code == status.HTTP_400_BAD_REQUEST
        assert "ai_api_key" in switched.data

        off = operator_client.patch(
            url,
            {"aiProvider": "", "aiModel": "", "aiApiKey": ""},
            format="json",
        )
        assert off.status_code == status.HTTP_200_OK
        assert off.data["ai_api_key_set"] is False


@pytest.mark.django_db
class TestChatKeyMovesIntoTheProfile:
    def test_0077_copies_the_key_and_blanks_the_old_column(self):
        from importlib import import_module

        migration = import_module(
            "tenant.migrations.0077_chat_api_key_into_ai_profile"
        )
        copy_sql, params = migration.Migration.operations[1].sql[0]
        with_key = store_tenant("ai_move_with_key")
        without_key = store_tenant("ai_move_without_key")
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT ai_api_key FROM tenant_tenant WHERE id = %s",
                [
                    store_tenant(
                        "ai_move_token_source",
                        ai_provider="gemini",
                        ai_model="m",
                        ai_api_key="old-chat-key",
                    ).pk
                ],
            )
            token = cursor.fetchone()[0]
            cursor.execute(
                "UPDATE tenant_tenant SET chat_api_key_encrypted = %s"
                " WHERE id = %s",
                [token, with_key.pk],
            )
            cursor.execute(copy_sql, params)
            cursor.execute(
                "SELECT chat_api_key_encrypted FROM tenant_tenant"
                " WHERE id IN (%s, %s)",
                [with_key.pk, without_key.pk],
            )
            assert {row[0] for row in cursor.fetchall()} == {""}

        with_key.refresh_from_db()
        assert with_key.ai_provider == AIProvider.GEMINI
        assert with_key.ai_model == "gemini-3.5-flash-lite"
        assert with_key.ai_api_key == "old-chat-key"
        without_key.refresh_from_db()
        assert without_key.ai_provider == ""
        assert without_key.ai_api_key == ""
