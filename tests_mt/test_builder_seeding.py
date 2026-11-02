"""A new store starts with the storefront's default documents, released once,
in its own schema, without asking the storefront anything."""

from __future__ import annotations

from unittest import mock

import pytest
from django_tenants.utils import schema_context

from builder import seeding
from builder.models import (
    BuilderDocument,
    BuilderReference,
    BuilderRelease,
    BuilderVersion,
    VersionKind,
)
from tenant import provisioning


@pytest.mark.django_db
def test_a_store_is_seeded_in_its_own_schema_with_one_release(
    mt_tenant, mt_tenant_b
):
    with (
        mock.patch("builder.storefront.requests.get") as storefront,
        schema_context(mt_tenant.schema_name),
    ):
        release = seeding.seed_store()

        assert storefront.call_count == 0
        assert release is not None
        assert release.number == 1
        seeds = seeding.load_seeds()
        assert set(release.versions) == {seed.key for seed in seeds}
        for seed in seeds:
            document = BuilderDocument.objects.get(key=seed.key)
            version = BuilderVersion.objects.get(pk=release.versions[seed.key])
            assert document.draft == seed.data == version.data
            assert document.draft_i18n == seed.i18n == version.i18n
            assert document.published_revision == document.draft_revision == 1
            assert version.kind == VersionKind.PUBLISH
            assert version.number == 1
        assert not BuilderReference.objects.exists()

    with schema_context(mt_tenant_b.schema_name):
        assert not BuilderDocument.objects.exists()
        assert not BuilderRelease.objects.exists()


@pytest.mark.django_db
def test_seeding_twice_changes_nothing(mt_tenant):
    with schema_context(mt_tenant.schema_name):
        seeding.seed_store()

        assert seeding.seed_store() is None

        assert BuilderRelease.objects.count() == 1
        assert BuilderDocument.objects.count() == len(seeding.load_seeds())


@pytest.mark.django_db
def test_a_store_with_documents_is_left_alone(mt_tenant):
    with schema_context(mt_tenant.schema_name):
        BuilderDocument.objects.create(
            kind="page", key="page:mine", draft={"sections": {}, "order": []}
        )

        assert seeding.seed_store() is None

        assert BuilderRelease.objects.count() == 0
        assert BuilderDocument.objects.count() == 1


@pytest.mark.django_db
def test_provisioning_seeds_the_builder_documents(mt_tenant):
    with schema_context(mt_tenant.schema_name):
        assert provisioning._seed_builder_documents(mt_tenant) is True

        assert BuilderRelease.objects.count() == 1
