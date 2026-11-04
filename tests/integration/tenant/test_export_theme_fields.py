"""``manage.py export_theme_fields`` reads each store's theme fields."""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.management import call_command

from tenant.management.commands.export_theme_fields import FIELDS
from tenant.models import Tenant
from tests.utils.staff import store_tenant

pytestmark = pytest.mark.django_db


def run(*args: str) -> dict:
    out = StringIO()
    call_command("export_theme_fields", *args, stdout=out)
    return json.loads(out.getvalue())


def test_a_store_is_exported_with_the_names_the_storefront_reads():
    store = store_tenant("export_theme_store")
    Tenant.objects.filter(pk=store.pk).update(
        theme_preset="default",
        primary_color="blue",
        neutral_color="slate",
        accent_hex="#112233",
        theme_metadata={"radius": "0.5rem"},
    )

    exported = run("--tenant", "export_theme_store")

    assert exported == {
        "stores": [
            {
                "schema": "export_theme_store",
                "fields": {
                    "schemaName": "export_theme_store",
                    "themePreset": "default",
                    "primaryColor": "blue",
                    "neutralColor": "slate",
                    "accentHex": "#112233",
                    "successHex": store.success_hex,
                    "warningHex": store.warning_hex,
                    "errorHex": store.error_hex,
                    "infoHex": store.info_hex,
                    "themeMetadata": {"radius": "0.5rem"},
                },
            }
        ]
    }


def test_the_platform_tenant_is_left_out_and_nothing_is_written():
    store_tenant("export_theme_other")
    before = list(Tenant.objects.order_by("pk").values_list("pk", "updated_at"))

    exported = run()

    schemas = {s["schema"] for s in exported["stores"]}
    assert "public" not in schemas
    assert "export_theme_other" in schemas
    assert (
        list(Tenant.objects.order_by("pk").values_list("pk", "updated_at"))
        == before
    )


def test_every_exported_field_is_a_tenant_column():
    assert all(Tenant._meta.get_field(column) for column in FIELDS)
