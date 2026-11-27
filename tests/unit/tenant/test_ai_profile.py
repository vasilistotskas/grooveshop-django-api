"""Per-store AI profile: provider catalog, validation, resolve payload,
write-only admin form, platform API serializer, and chat-key backfill."""

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
    AI_PROVIDERS,
    LEGACY_CHAT_DEFAULT_MODEL,
    LEGACY_CHAT_DEFAULT_PROVIDER,
    get_ai_provider,
    legacy_chat_api_key,
    serialize_ai_profile,
)
from tenant.auth_backends import PLATFORM_IDENTITY_ATTR
from tenant.cache import tenant_resolve_key
from tenant.enum import AIProvider, TenantMembershipRole
from tenant.models import Tenant, TenantDomain, UserTenantMembership
from tests.utils.staff import store_tenant
from user.factories.account import UserAccountFactory


def _request(user):
    request = RequestFactory().get("/admin/")
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


class TestAIProviderCatalog:
    def test_every_enum_choice_has_an_https_spec(self):
        assert set(AI_PROVIDERS) == set(AIProvider)
        for code, spec in AI_PROVIDERS.items():
            assert spec.code is code
            assert spec.base_url.startswith("https://")
            assert spec.base_url.endswith("/")
            assert isinstance(spec.supports_tool_calling, bool)
            assert isinstance(spec.supports_structured_outputs, bool)

    def test_get_ai_provider_returns_none_for_unknown_or_empty(self):
        assert get_ai_provider("") is None
        assert get_ai_provider("https://evil.example/v1/") is None
        assert get_ai_provider("custom") is None
        gemini = get_ai_provider("gemini")
        assert gemini is not None
        assert (
            gemini.base_url
            == "https://generativelanguage.googleapis.com/v1beta/openai/"
        )


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
            "supports_tool_calling": True,
            "supports_structured_outputs": True,
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
            tenant.clean()
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
        "bad_key", [" sk-padded", "sk-padded ", "sk-\nkey"]
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
        url = "/api/v1/tenant/resolve?domain=ai-openai.example"

        public = client.get(url)
        assert public.status_code == 200
        assert "aiProfile" not in public.json()
        assert "sk-openai-resolve-secret" not in public.content.decode()

        forged = client.get(url, HTTP_X_INTERNAL_TOKEN="wrong")
        assert "aiProfile" not in forged.json()

        gateway = client.get(url, HTTP_X_INTERNAL_TOKEN="gw-secret")
        assert gateway.status_code == 200
        assert gateway["Cache-Control"] == "private, no-store"
        assert "X-Internal-Token" in gateway["Vary"]
        payload = gateway.json()
        assert payload["aiProfile"] == {
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1/",
            "model": "gpt-4.1-mini",
            "apiKey": "sk-openai-resolve-secret",
            "supportsToolCalling": True,
            "supportsStructuredOutputs": True,
        }
        # Never forward an OpenAI key on the legacy Gemini-only chatApiKey field.
        assert payload["chatApiKey"] == ""

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
            "/api/v1/tenant/resolve?domain=ai-none.example",
            HTTP_X_INTERNAL_TOKEN="gw-secret",
        )
        assert response.status_code == 200
        assert response.json()["aiProfile"] is None

    def test_legacy_chat_api_key_bridge_mirrors_only_gemini(self):
        gemini = store_tenant(
            "ai_bridge_gemini",
            ai_provider=AIProvider.GEMINI,
            ai_model="gemini-3.5-flash-lite",
            ai_api_key="gem-key",
        )
        assert legacy_chat_api_key(gemini) == "gem-key"

        mistral = store_tenant(
            "ai_bridge_mistral",
            ai_provider=AIProvider.MISTRAL,
            ai_model="mistral-small-latest",
            ai_api_key="mis-key",
            chat_api_key="old-gem-key",
        )
        assert legacy_chat_api_key(mistral) == ""

        legacy_only = store_tenant(
            "ai_bridge_legacy",
            chat_api_key="only-old-key",
        )
        assert legacy_chat_api_key(legacy_only) == "only-old-key"


@pytest.mark.django_db
class TestTenantAdminWriteOnlyAIKey:
    def _admin(self):
        return admin_site._registry[Tenant]

    def test_ai_fields_are_editable_by_store_owner(
        self, bind_tenant_and_schema
    ):
        tenant = store_tenant("ai_admin_scope")
        owner = _store_owner(tenant)
        bind_tenant_and_schema(tenant)
        request = _request(owner)

        readonly = set(self._admin().get_readonly_fields(request, tenant))
        assert "ai_provider" not in readonly
        assert "ai_model" not in readonly
        assert "ai_api_key" not in readonly
        assert "ai_api_key_is_set" in readonly

        listed = [
            field
            for _name, options in self._admin().get_fieldsets(request, tenant)
            for field in options["fields"]
        ]
        assert "ai_provider" in listed
        assert "ai_model" in listed
        assert "ai_api_key" in listed
        assert "ai_api_key_is_set" in listed
        # No saved key yet, so the clear checkbox is omitted.
        assert "clear_ai_api_key" not in listed

    def test_clear_checkbox_shown_only_when_key_is_saved(
        self, bind_tenant_and_schema
    ):
        tenant = store_tenant(
            "ai_admin_clear_vis",
            ai_provider=AIProvider.GEMINI,
            ai_model="gemini-3.5-flash-lite",
            ai_api_key="secret-saved-key",
        )
        owner = _store_owner(tenant)
        bind_tenant_and_schema(tenant)
        listed = [
            field
            for _name, options in self._admin().get_fieldsets(
                _request(owner), tenant
            )
            for field in options["fields"]
        ]
        assert "clear_ai_api_key" in listed

    def test_admin_form_never_renders_stored_key_and_preserves_it_when_blank(
        self, bind_tenant_and_schema
    ):
        secret = "sk-never-render-this-plaintext-ai-key"
        tenant = store_tenant(
            "ai_admin_writeonly",
            ai_provider=AIProvider.OPENAI,
            ai_model="gpt-4.1-mini",
            ai_api_key=secret,
        )
        owner = _store_owner(tenant)
        bind_tenant_and_schema(tenant)
        request = _request(owner)

        form_class = self._admin().get_form(
            request,
            tenant,
            fields=[
                "store_name",
                "ai_provider",
                "ai_model",
                "ai_api_key",
                "clear_ai_api_key",
            ],
        )
        unbound = form_class(instance=tenant)
        rendered = unbound.as_p()
        assert 'type="password"' in rendered
        assert secret not in rendered
        assert unbound.initial["ai_api_key"] == ""

        # Editing an unrelated field (or model) with blank key input keeps the key.
        bound = form_class(
            {
                "store_name": "Updated Store",
                "ai_provider": AIProvider.OPENAI,
                "ai_model": "gpt-4.1",
                "ai_api_key": "",
                "clear_ai_api_key": False,
            },
            instance=tenant,
        )
        assert bound.is_valid(), bound.errors
        bound.save()

        tenant.refresh_from_db()
        assert tenant.store_name == "Updated Store"
        assert tenant.ai_model == "gpt-4.1"
        assert tenant.ai_api_key == secret

    def test_admin_form_replaces_key_encrypted_and_clears_explicitly(
        self, bind_tenant_and_schema
    ):
        tenant = store_tenant(
            "ai_admin_rotate",
            ai_provider=AIProvider.GEMINI,
            ai_model="gemini-3.5-flash-lite",
            ai_api_key="old-key",
            chat_api_key="old-key",
        )
        user = _superuser()
        bind_tenant_and_schema(None)
        request = _request(user)
        form_class = self._admin().get_form(
            request,
            tenant,
            fields=[
                "ai_provider",
                "ai_model",
                "ai_api_key",
                "clear_ai_api_key",
            ],
        )

        # Cannot both enter a new key and tick clear.
        conflict = form_class(
            {
                "ai_provider": AIProvider.OPENAI,
                "ai_model": "gpt-4.1-mini",
                "ai_api_key": "new-key",
                "clear_ai_api_key": True,
            },
            instance=tenant,
        )
        assert not conflict.is_valid()
        assert "clear_ai_api_key" in conflict.errors
        assert "old-key" not in conflict.as_p()
        assert "new-key" not in conflict.as_p()

        # Replace with a new key: stored as ciphertext.
        rotate = form_class(
            {
                "ai_provider": AIProvider.MISTRAL,
                "ai_model": "mistral-small-latest",
                "ai_api_key": "sk-mistral-new",
                "clear_ai_api_key": False,
            },
            instance=tenant,
        )
        assert rotate.is_valid(), rotate.errors
        rotate.save()

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT ai_api_key FROM tenant_tenant WHERE id = %s",
                [tenant.pk],
            )
            raw = cursor.fetchone()[0]
        assert raw and "sk-mistral-new" not in raw
        tenant.refresh_from_db()
        assert tenant.ai_api_key == "sk-mistral-new"

        # Clear key + provider + model disables AI and clears legacy chat_api_key.
        clear_form = form_class(
            {
                "ai_provider": "",
                "ai_model": "",
                "ai_api_key": "",
                "clear_ai_api_key": True,
            },
            instance=tenant,
        )
        assert clear_form.is_valid(), clear_form.errors
        clear_form.save()

        tenant.refresh_from_db()
        assert tenant.ai_provider == ""
        assert tenant.ai_model == ""
        assert tenant.ai_api_key == ""
        assert tenant.chat_api_key == ""

    def test_change_view_html_never_leaks_saved_ai_api_key(
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
            assert response.status_code == 200
            html = response.content.decode()
            assert secret not in html
            assert 'name="ai_api_key"' in html
            assert 'type="password"' in html
            assert 'name="clear_ai_api_key"' in html


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

    def test_patch_sets_profile_write_only_and_refuses_partial(
        self, operator_client
    ):
        tenant = store_tenant("ai_api_patch")
        url = reverse("tenant-admin-detail", args=[tenant.pk])

        bad = operator_client.patch(
            url,
            {"aiProvider": "openai", "aiModel": "gpt-4.1-mini"},
            format="json",
        )
        assert bad.status_code == status.HTTP_400_BAD_REQUEST
        assert "ai_api_key" in bad.data

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
        assert ok.data["ai_provider"] == "openai"
        assert ok.data["ai_model"] == "gpt-4.1-mini"
        assert ok.data["ai_api_key_set"] is True
        assert "ai_api_key" not in ok.data

        get_resp = operator_client.get(url)
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.data["ai_api_key_set"] is True
        assert "ai_api_key" not in get_resp.data
        assert "sk-api-write-only" not in get_resp.content.decode()

        tenant.refresh_from_db()
        assert tenant.ai_api_key == "sk-api-write-only"


@pytest.mark.django_db
class TestBackfillAIProfileMigration:
    def test_backfill_copies_chat_key_and_is_idempotent(self):
        from importlib import import_module

        mod = import_module("tenant.migrations.0077_backfill_tenant_ai_profile")
        legacy = store_tenant("ai_bf_legacy", chat_api_key="legacy-gem-key")
        empty = store_tenant("ai_bf_empty")
        custom = store_tenant(
            "ai_bf_custom",
            chat_api_key="old-key",
            ai_provider=AIProvider.OPENAI,
            ai_model="gpt-4.1-mini",
            ai_api_key="custom-openai-key",
        )

        with connection.schema_editor() as schema_editor:
            mod.backfill_ai_profile_from_chat_key(None, schema_editor)
            # Re-running is a no-op.
            mod.backfill_ai_profile_from_chat_key(None, schema_editor)

        legacy.refresh_from_db()
        assert legacy.ai_provider == LEGACY_CHAT_DEFAULT_PROVIDER
        assert legacy.ai_model == LEGACY_CHAT_DEFAULT_MODEL
        assert legacy.ai_api_key == "legacy-gem-key"

        empty.refresh_from_db()
        assert empty.ai_provider == ""
        assert empty.ai_model == ""
        assert empty.ai_api_key == ""

        custom.refresh_from_db()
        assert custom.ai_provider == AIProvider.OPENAI
        assert custom.ai_model == "gpt-4.1-mini"
        assert custom.ai_api_key == "custom-openai-key"
