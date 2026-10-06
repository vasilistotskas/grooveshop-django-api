"""``Tenant.store_description_i18n`` — the store line in the other locales.

``store_description`` is the default locale's wording; this JSON map
carries the rest, the way ``AUTH_PANEL`` keeps ``{"tagline", "i18n"}``.
What is worth pinning: the validation (storefront locales only, string
values, never the default locale), the merchant-edit wiring, and the
serializer field being OPTIONAL rather than read-only, so a
frontend-first deploy cannot 503 the platform (see
``test_openai_pixel_id``).
"""

from __future__ import annotations

import pytest
from django.contrib.admin import site as admin_site
from django.core.exceptions import ValidationError
from django.forms import modelform_factory
from django.test import RequestFactory

from tenant.models import Tenant
from tenant.role_scopes import TENANT_SELF_EDITABLE_FIELDS
from tenant.serializers import TenantAdminSerializer, TenantConfigSerializer
from tests.utils.staff import store_tenant


def _tenant(**kwargs) -> Tenant:
    defaults = {
        "schema_name": "i18n_line",
        "name": "I18n Line",
        "slug": "i18n-line",
        "owner_email": "owner@i18n-line.example.com",
    }
    defaults.update(kwargs)
    return Tenant(**defaults)


class TestValidation:
    def test_default_is_an_empty_map(self):
        assert _tenant().store_description_i18n == {}
        _tenant().clean_fields(exclude=["schema_name"])

    def test_accepts_another_storefront_locale(self):
        tenant = _tenant(
            default_locale="el",
            store_description_i18n={"en": "Phone accessories"},
        )
        tenant.clean_fields()
        tenant.clean()

    def test_rejects_a_locale_the_storefront_has_no_routes_for(self):
        # ``de`` is a Django content language, not a storefront one.
        tenant = _tenant(store_description_i18n={"de": "Handyzubehoer"})
        with pytest.raises(ValidationError) as exc_info:
            tenant.clean_fields()
        assert "store_description_i18n" in exc_info.value.message_dict

    def test_rejects_non_string_values(self):
        tenant = _tenant(store_description_i18n={"en": 7})
        with pytest.raises(ValidationError) as exc_info:
            tenant.clean_fields()
        assert "store_description_i18n" in exc_info.value.message_dict

    def test_rejects_a_non_object(self):
        tenant = _tenant(store_description_i18n=["en"])
        with pytest.raises(ValidationError) as exc_info:
            tenant.clean_fields()
        assert "store_description_i18n" in exc_info.value.message_dict

    def test_rejects_the_default_locale(self):
        # That line is ``store_description``; a copy here is never read.
        tenant = _tenant(
            default_locale="el", store_description_i18n={"el": "Duplicate"}
        )
        with pytest.raises(ValidationError) as exc_info:
            tenant.clean()
        assert "store_description_i18n" in exc_info.value.message_dict

    def test_default_locale_is_per_tenant(self):
        # An English-default store keeps Greek here and may not keep en.
        english = _tenant(
            default_locale="en", store_description_i18n={"el": "Ελληνικά"}
        )
        english.clean()
        clash = _tenant(
            default_locale="en", store_description_i18n={"en": "Dup"}
        )
        with pytest.raises(ValidationError):
            clash.clean()


class TestSerializerContract:
    def test_public_field_is_optional_not_read_only(self):
        field = TenantConfigSerializer().fields["store_description_i18n"]
        assert not field.read_only
        assert not field.required

    def test_public_payload_carries_the_map(self):
        tenant = _tenant(store_description_i18n={"en": "Phone accessories"})
        field = TenantConfigSerializer().fields["store_description_i18n"]
        assert field.to_representation(field.get_attribute(tenant)) == {
            "en": "Phone accessories"
        }

    def test_admin_serializer_exposes_it(self):
        assert "store_description_i18n" in TenantAdminSerializer().fields


@pytest.mark.django_db
class TestAdmin:
    def test_the_branding_tab_edits_it_next_to_the_description(self):
        model_admin = admin_site._registry[Tenant]
        fields = [
            name
            for _title, options in model_admin.fieldsets
            for name in options["fields"]
        ]
        assert fields.index("store_description_i18n") == (
            fields.index("store_description") + 1
        )

    def test_the_admin_form_saves_it(self):
        model_admin = admin_site._registry[Tenant]
        request = RequestFactory().get("/admin/")
        form_class = modelform_factory(
            Tenant,
            fields=["default_locale", "store_description_i18n"],
            formfield_callback=lambda field, **kwargs: (
                model_admin.formfield_for_dbfield(field, request, **kwargs)
            ),
        )
        assert type(
            form_class.base_fields["store_description_i18n"]
        ).__name__ == ("UnfoldAdminJSONSchemaField")
        tenant = store_tenant("i18n_line_form")
        form = form_class(
            {
                "default_locale": "el",
                "store_description_i18n": '{"en": "Phone accessories"}',
            },
            instance=tenant,
        )
        assert form.is_valid(), form.errors
        form.save()
        assert Tenant.objects.get(pk=tenant.pk).store_description_i18n == {
            "en": "Phone accessories"
        }

    def test_the_admin_form_refuses_an_unknown_locale(self):
        model_admin = admin_site._registry[Tenant]
        request = RequestFactory().get("/admin/")
        form_class = modelform_factory(
            Tenant,
            fields=["default_locale", "store_description_i18n"],
            formfield_callback=lambda field, **kwargs: (
                model_admin.formfield_for_dbfield(field, request, **kwargs)
            ),
        )
        form = form_class(
            {
                "default_locale": "el",
                "store_description_i18n": '{"fr": "Accessoires"}',
            },
            instance=store_tenant("i18n_line_form_bad"),
        )
        assert not form.is_valid()
        assert "store_description_i18n" in form.errors

    def test_a_store_owner_may_edit_it_like_the_description(self):
        assert "store_description" in TENANT_SELF_EDITABLE_FIELDS
        assert "store_description_i18n" in TENANT_SELF_EDITABLE_FIELDS

    def test_saving_moves_the_resolve_cache_generation(self):
        tenant = store_tenant("i18n_line_cache")
        before = Tenant.objects.get(pk=tenant.pk).cache_generation
        tenant.store_description_i18n = {"en": "Phone accessories"}
        tenant.save()
        after = Tenant.objects.get(pk=tenant.pk)
        assert after.cache_generation > before
        assert after.store_description_i18n == {"en": "Phone accessories"}
