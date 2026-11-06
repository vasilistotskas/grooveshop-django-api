"""``core.json_schema``: one schema per structured JSON field, used by
the validator, the admin editor and migrations alike."""

from __future__ import annotations

import pytest
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.test import RequestFactory
from unfold.fields import UnfoldAdminJSONSchemaField

from core.json_schema import JSONSchemaValidator, schema_validator
from tenant.models import Tenant


def test_errors_name_the_offending_entry():
    validator = JSONSchemaValidator(
        "tenant.json_schemas.store_description_i18n"
    )

    with pytest.raises(ValidationError) as excinfo:
        validator({"el": 1, "en": 2})

    messages = excinfo.value.messages
    assert any(m.startswith("el:") for m in messages)
    assert any(m.startswith("en:") for m in messages)


def test_a_valid_value_passes():
    JSONSchemaValidator("tenant.json_schemas.store_description_i18n")(
        {"en": "A store"}
    )


def test_migrations_record_the_path_not_the_schema():
    path, args, kwargs = JSONSchemaValidator(
        "tenant.json_schemas.store_description_i18n"
    ).deconstruct()
    assert path == "core.json_schema.JSONSchemaValidator"
    assert args == ("tenant.json_schemas.store_description_i18n",)
    assert kwargs == {}


def test_the_field_finds_its_validator():
    field = Tenant._meta.get_field("store_description_i18n")
    assert schema_validator(field) == JSONSchemaValidator(
        "tenant.json_schemas.store_description_i18n"
    )
    assert schema_validator(Tenant._meta.get_field("name")) is None


@pytest.mark.django_db
def test_the_admin_edits_it_with_the_schema_form():
    from user.factories.account import UserAccountFactory

    request = RequestFactory().get("/")
    request.user = UserAccountFactory(admin=True)
    tenant_admin = admin.site._registry[Tenant]

    field = tenant_admin.formfield_for_dbfield(
        Tenant._meta.get_field("store_description_i18n"), request
    )

    assert isinstance(field, UnfoldAdminJSONSchemaField)
    assert field.schema["properties"]["en"]["type"] == "string"
