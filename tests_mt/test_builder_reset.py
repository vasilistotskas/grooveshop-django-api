"""``builder_reset`` against real store schemas: it reports by default,
deletes only with ``--confirm``, touches only the store it names, and
refuses a store whose builder data is in use."""

from __future__ import annotations

import pytest
from django.core.management import CommandError, call_command
from django_tenants.utils import schema_context

from builder.factories import (
    BuilderDocumentFactory,
    BuilderReleaseFactory,
    BuilderVersionFactory,
)
from builder.models import BuilderDocument, BuilderRelease, VersionKind


def an_import(tenant) -> None:
    with schema_context(tenant.schema_name):
        document = BuilderDocumentFactory(
            key="template:home",
            kind="template",
            draft_revision=1,
            published_revision=1,
        )
        version = BuilderVersionFactory(
            document=document, kind=VersionKind.PUBLISH, number=1
        )
        BuilderReleaseFactory(number=1, versions={document.key: version.pk})


def documents(tenant) -> int:
    with schema_context(tenant.schema_name):
        return BuilderDocument.objects.count()


@pytest.mark.django_db
def test_without_confirm_nothing_is_deleted(mt_tenant, capsys):
    an_import(mt_tenant)

    call_command("builder_reset", tenant=mt_tenant.schema_name)

    assert documents(mt_tenant) > 0
    assert "pass --confirm" in capsys.readouterr().out


@pytest.mark.django_db
def test_confirm_deletes_the_named_store_only(mt_tenant, mt_tenant_b):
    an_import(mt_tenant)
    an_import(mt_tenant_b)
    other = documents(mt_tenant_b)

    call_command("builder_reset", tenant=mt_tenant.schema_name, confirm=True)

    assert documents(mt_tenant) == 0
    assert documents(mt_tenant_b) == other


@pytest.mark.django_db
def test_a_store_in_use_is_refused(mt_tenant):
    an_import(mt_tenant)
    with schema_context(mt_tenant.schema_name):
        BuilderRelease.objects.create(number=2, versions={})

    with pytest.raises(CommandError, match="in use"):
        call_command(
            "builder_reset", tenant=mt_tenant.schema_name, confirm=True
        )

    assert documents(mt_tenant) > 0
