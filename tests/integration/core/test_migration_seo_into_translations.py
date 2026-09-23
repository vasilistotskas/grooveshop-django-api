"""Exercises the three ``*_copy_seo_into_translations`` data migrations.

The shared-row ``seo_*`` columns only exist BEFORE each app's
``remove_shared_seo_fields`` migration, so unlike the lightweight
harness in ``test_migration_seed_legal_documents`` this one rolls the
app back to its ``seo_translated_fields`` state with the real
``MigrationExecutor``, writes rows through the historical models, and
runs the RunPython callables against that state. PostgreSQL DDL is
transactional, so the ``TestCase`` transaction undoes the rollback and
leaves the worker's database at the latest migration again.

What must hold: every non-empty value lands in the translation for the
TENANT's ``default_locale`` (not the platform-wide parler default),
the translation row is created when that language has none, an empty
row gets nothing, and the reverse puts the values back on the shared
row.
"""

from __future__ import annotations

import importlib

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase

from tenant.models import Tenant

SCHEMA_NAME = "seo_migration_test"


class _FakeConnection:
    """Only what the callables read: the schema being migrated."""

    schema_name = SCHEMA_NAME
    alias = connection.alias


class _FakeSchemaEditor:
    connection = _FakeConnection()


def _state_before_copy(app_label: str, target: str):
    """Roll ``app_label`` back to ``target``; return the whole project's
    apps at that point, as ``migrate`` hands them to a RunPython (every
    other app at its latest — the callables read ``tenant.Tenant``)."""
    executor = MigrationExecutor(connection)
    executor.migrate([(app_label, target)])
    executor.loader.build_graph()
    nodes = [
        node
        for node in executor.loader.graph.leaf_nodes()
        if node[0] != app_label
    ]
    return executor.loader.project_state([*nodes, (app_label, target)]).apps


class TestProductSeoMigration(TestCase):
    copy = importlib.import_module(
        "product.migrations.0045_copy_seo_into_translations"
    )

    def setUp(self):
        # English, on purpose: the platform default is Greek, so a copy
        # into "en" can only come from the tenant row.
        tenant = Tenant(
            schema_name=SCHEMA_NAME,
            name="SEO migration test",
            slug="seo-migration-test",
            owner_email="seo-migration@example.com",
            default_locale="en",
        )
        tenant.auto_create_schema = False
        tenant.save()
        self.apps = _state_before_copy("product", "0044_seo_translated_fields")
        self.Product = self.apps.get_model("product", "Product")
        self.ProductTranslation = self.apps.get_model(
            "product", "ProductTranslation"
        )
        self.ProductCategory = self.apps.get_model("product", "ProductCategory")
        self.CategoryTranslation = self.apps.get_model(
            "product", "ProductCategoryTranslation"
        )

    def _category(self, slug: str, **seo):
        return self.ProductCategory.objects.create(
            slug=slug, lft=1, rght=2, tree_id=1, level=0, **seo
        )

    def test_copies_into_the_tenants_language_and_creates_the_row(self):
        product = self.Product.objects.create(
            slug="seo-product",
            sku="seo-product",
            shared_seo_title="Phone case",
            shared_seo_description="A case that survives a drop.",
            shared_seo_keywords="case, phone",
        )
        # Only a Greek translation exists; the English one is created.
        self.ProductTranslation.objects.create(
            master_id=product.pk, language_code="el", name="Θήκη"
        )

        self.copy.copy_seo_into_translations(self.apps, _FakeSchemaEditor)

        english = self.ProductTranslation.objects.get(
            master_id=product.pk, language_code="en"
        )
        assert english.seo_title == "Phone case"
        assert english.seo_description == "A case that survives a drop."
        assert english.seo_keywords == "case, phone"
        greek = self.ProductTranslation.objects.get(
            master_id=product.pk, language_code="el"
        )
        assert greek.name == "Θήκη"
        assert greek.seo_title == ""

    def test_updates_an_existing_translation_in_place(self):
        category = self._category("seo-category", shared_seo_title="Chargers")
        self.CategoryTranslation.objects.create(
            master_id=category.pk, language_code="en", name="Chargers"
        )

        self.copy.copy_seo_into_translations(self.apps, _FakeSchemaEditor)

        rows = self.CategoryTranslation.objects.filter(master_id=category.pk)
        assert rows.count() == 1
        assert rows.get().name == "Chargers"
        assert rows.get().seo_title == "Chargers"

    def test_an_empty_row_gets_no_translation(self):
        category = self._category("no-seo")

        self.copy.copy_seo_into_translations(self.apps, _FakeSchemaEditor)

        assert not self.CategoryTranslation.objects.filter(
            master_id=category.pk
        ).exists()

    def test_reverse_puts_the_values_back_on_the_shared_row(self):
        product = self.Product.objects.create(
            slug="seo-back", sku="seo-back", shared_seo_title="Cable"
        )
        self.copy.copy_seo_into_translations(self.apps, _FakeSchemaEditor)
        self.Product.objects.filter(pk=product.pk).update(shared_seo_title="")

        self.copy.copy_seo_back_to_shared_row(self.apps, _FakeSchemaEditor)

        product.refresh_from_db()
        assert product.shared_seo_title == "Cable"

    def test_a_schema_without_a_tenant_row_is_refused(self):
        Tenant.objects.filter(schema_name=SCHEMA_NAME).delete()
        self.Product.objects.create(
            slug="orphan", sku="orphan", shared_seo_title="Orphan"
        )

        with self.assertRaisesRegex(RuntimeError, "no tenant"):
            self.copy.copy_seo_into_translations(self.apps, _FakeSchemaEditor)


class TestPageLayoutSeoMigration(TestCase):
    copy = importlib.import_module(
        "page_config.migrations.0029_copy_seo_into_translations"
    )

    def setUp(self):
        tenant = Tenant(
            schema_name=SCHEMA_NAME,
            name="SEO migration test",
            slug="seo-migration-test",
            owner_email="seo-migration@example.com",
            default_locale="el",
        )
        tenant.auto_create_schema = False
        tenant.save()
        self.apps = _state_before_copy(
            "page_config", "0028_seo_translated_fields"
        )

    def test_a_layout_gets_its_first_translation_row(self):
        PageLayout = self.apps.get_model("page_config", "PageLayout")
        PageLayoutTranslation = self.apps.get_model(
            "page_config", "PageLayoutTranslation"
        )
        layout = PageLayout.objects.create(
            page_type="home",
            title="Homepage",
            shared_seo_title="Κατάστημα | Αρχική",
            shared_seo_description="Η αρχική σελίδα.",
        )

        self.copy.copy_seo_into_translations(self.apps, _FakeSchemaEditor)

        translation = PageLayoutTranslation.objects.get(master_id=layout.pk)
        assert translation.language_code == "el"
        assert translation.seo_title == "Κατάστημα | Αρχική"
        assert translation.seo_description == "Η αρχική σελίδα."
