"""Every URL the API stores or takes is one a WHATWG URL parser reads.

Django's and DRF's ``URLField`` accept ``https://example.com:65536``; the
storefront's ``z.url()`` refuses it, so one such value saved would fail
the storefront's parse (a 422) on every response that carries it
(django issue 553). ``core.fields.url.WebURLField`` and
``core.api.url_field.WebURLField`` refuse it, and the source holds no
other URL field.
"""

import ast
from pathlib import Path

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models
from rest_framework import serializers

from core.api.url_field import WebURLField as WebURLSerializerField
from core.fields.url import WebURLField
from tenant.models import Tenant

ROOT = Path(__file__).resolve().parents[3]
OUT_OF_RANGE = "https://example.com:65536/x"
FINE = "https://example.com:8443/x"
#: The two modules that define the fields.
DEFINITIONS = {
    ROOT / "core" / "fields" / "url.py",
    ROOT / "core" / "api" / "url_field.py",
}
EXCLUDED_PARTS = {".venv", "migrations", "tests", "tests_mt", "node_modules"}


def _bare_url_fields() -> list[str]:
    """``models.URLField(`` / ``serializers.URLField(`` / ``forms.URLField(``
    calls outside the two definitions."""
    found = []
    for path in ROOT.rglob("*.py"):
        if EXCLUDED_PARTS & set(path.relative_to(ROOT).parts):
            continue
        if path in DEFINITIONS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "URLField"
            ):
                found.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    return found


def test_the_source_declares_no_other_url_field():
    assert _bare_url_fields() == []


def _project_models():
    """The models of this project's own apps, not of installed libraries."""
    return [
        model
        for config in apps.get_app_configs()
        if Path(config.path).resolve().is_relative_to(ROOT)
        and ".venv" not in Path(config.path).resolve().parts
        for model in config.get_models()
    ]


def test_every_model_url_field_is_a_web_url_field():
    models_seen = _project_models()
    plain = [
        f"{model._meta.label}.{field.name}"
        for model in models_seen
        for field in model._meta.get_fields()
        if isinstance(field, models.URLField)
        and not isinstance(field, WebURLField)
    ]

    # Canary: the walk reaches the apps that hold URL fields.
    assert {"tenant.Tenant", "user.UserAccount"} <= {
        model._meta.label for model in models_seen
    }
    assert plain == []


def test_a_model_url_refuses_a_port_out_of_range():
    field = Tenant._meta.get_field("logo_light_url")

    field.run_validators(FINE)
    with pytest.raises(ValidationError):
        field.run_validators(OUT_OF_RANGE)


def test_a_model_serializer_refuses_it_too():
    class TenantLogoSerializer(serializers.ModelSerializer):
        class Meta:
            model = Tenant
            fields = ("logo_light_url",)

    assert isinstance(
        TenantLogoSerializer().fields["logo_light_url"],
        WebURLSerializerField,
    )
    assert TenantLogoSerializer(data={"logo_light_url": FINE}).is_valid()
    refused = TenantLogoSerializer(data={"logo_light_url": OUT_OF_RANGE})
    assert not refused.is_valid()
    assert refused.errors["logo_light_url"][0].code == "invalid"


def test_a_declared_serializer_field_refuses_it():
    field = WebURLSerializerField()

    assert field.run_validation(FINE) == FINE
    with pytest.raises(serializers.ValidationError):
        field.run_validation(OUT_OF_RANGE)
