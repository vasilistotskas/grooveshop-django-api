"""``builder_import_legacy`` against a real store schema: the command
finds the store, converts, writes in that schema only, and says no to a
second run."""

from __future__ import annotations

from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django_tenants.utils import schema_context

from builder.convert import Converted
from builder.models import BuilderDocument, BuilderRelease
from builder.storefront import clear_catalogue_cache
from page_config.models import PageLayout, PageSection
from tests.utils.builder import FakeCatalogue


def convert(body):
    sections = {
        s["id"]: {"type": "banner@1", "settings": {"text": "x"}}
        for s in body["legacy"]["sections"]
    }
    return Converted(
        document={"sections": sections, "order": list(sections)}, i18n={}
    )


@pytest.fixture(autouse=True)
def _storefront(settings):
    settings.NUXT_INTERNAL_BASE_URL = "http://storefront.test"
    clear_catalogue_cache()
    with (
        mock.patch(
            "builder.storefront.requests.get", side_effect=FakeCatalogue().get
        ),
        mock.patch("builder.importing.convert_legacy", side_effect=convert),
    ):
        yield
    clear_catalogue_cache()


@pytest.mark.django_db
def test_a_store_is_imported_in_its_own_schema_and_only_once(
    mt_tenant, mt_tenant_b
):
    with schema_context(mt_tenant.schema_name):
        layout = PageLayout.objects.create(
            page_type="home", title="Home", is_published=True
        )
        PageSection.objects.create(layout=layout, component_type="spacer")

    call_command("builder_import_legacy", tenant=mt_tenant.schema_name)

    with schema_context(mt_tenant.schema_name):
        assert BuilderDocument.objects.filter(key="template:home").exists()
        assert BuilderRelease.objects.get().number == 1
    with schema_context(mt_tenant_b.schema_name):
        assert not BuilderDocument.objects.exists()

    with pytest.raises(CommandError):
        call_command("builder_import_legacy", tenant=mt_tenant.schema_name)


@pytest.mark.django_db
def test_a_dry_run_leaves_the_store_untouched(mt_tenant):
    with schema_context(mt_tenant.schema_name):
        PageLayout.objects.create(
            page_type="vision", title="V", is_published=True
        )

    call_command(
        "builder_import_legacy", tenant=mt_tenant.schema_name, dry_run=True
    )

    with schema_context(mt_tenant.schema_name):
        assert not BuilderDocument.objects.exists()
