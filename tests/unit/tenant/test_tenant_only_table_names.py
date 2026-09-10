"""The table set that ``0036_drop_public_tenant_residue`` drops.

Getting this set wrong is not a cosmetic error: the migration issues one
``DROP TABLE`` without CASCADE, so a table missing from the set holds a
foreign key INTO it from outside and aborts the whole migration — and a
table wrongly IN the set would be dropped from a live schema.

The auto-created case is the one that actually bit. Scanning with a
plain ``apps.get_models()`` returned 138 tables and left 11 implicit
M2M through tables outside the set, every one of them referencing a
table inside it. ``include_auto_created=True`` returns 145 and brings
inbound-FK-from-outside to zero. Both figures measured against
production on 2026-09-10.
"""

from __future__ import annotations

from django.apps import apps
from django.conf import settings

from tenant.app_labels import tenant_only_app_labels, tenant_only_table_names


class TestAutoCreatedThroughTables:
    def test_implicit_m2m_through_tables_are_included(self):
        """The regression. ``blog_blogpost_tags`` has no model class,
        only a FK into ``blog_blogpost`` which IS in the set."""
        tables = tenant_only_table_names()

        assert "blog_blogpost_tags" in tables
        assert "blog_blogpost" in tables

    def test_the_set_is_larger_than_a_plain_model_scan(self):
        """Pins the reason the flag is there, so removing it fails."""
        labels = set(tenant_only_app_labels())
        without_auto_created = {
            model._meta.db_table
            for model in apps.get_models()
            if model._meta.managed and model._meta.app_label in labels
        }

        assert tenant_only_table_names() > without_auto_created

    def test_every_through_table_of_a_tenant_model_is_covered(self):
        """Generalises the two cases above: no M2M of a tenant-only
        model may fall outside the set, whatever it is called."""
        labels = set(tenant_only_app_labels())
        tables = tenant_only_table_names()

        for model in apps.get_models():
            if model._meta.app_label not in labels:
                continue
            for field in model._meta.local_many_to_many:
                through = field.remote_field.through
                if through._meta.auto_created and through._meta.managed:
                    assert through._meta.db_table in tables, (
                        f"{model._meta.db_table}.{field.name} through table "
                        f"{through._meta.db_table} is outside the set"
                    )


class TestSharedAppsAreNeverInScope:
    def test_an_app_in_both_lists_is_excluded(self):
        """``user``, ``sessions`` and ``usersessions`` are in SHARED_APPS
        AND TENANT_APPS. Their public tables are the control plane's own
        — dropping them would take out platform authentication."""
        tables = tenant_only_table_names()

        assert "user_useraccount" not in tables
        assert "django_session" not in tables

    def test_no_shared_only_app_contributes_a_table(self):
        shared_only = {
            app.split(".")[-1] for app in settings.SHARED_APPS
        } - set(tenant_only_app_labels())
        shared_tables = {
            model._meta.db_table
            for model in apps.get_models(include_auto_created=True)
            if model._meta.app_label in shared_only
        }

        assert tenant_only_table_names() & shared_tables == set()

    def test_the_tenant_registry_itself_is_never_in_scope(self):
        """``tenant`` is SHARED-only and owns the control plane. If this
        ever appeared in the set the migration would delete the tenant
        list while running against it."""
        tables = tenant_only_table_names()

        assert "tenant_tenant" not in tables
        assert "tenant_tenantdomain" not in tables


class TestKnownTenantTables:
    def test_representative_tenant_tables_are_in_scope(self):
        tables = tenant_only_table_names()

        for table in (
            "pay_way_payway",
            "order_order",
            "product_product",
            "cart_cart",
        ):
            assert table in tables

    def test_only_managed_tables_are_listed(self):
        """An unmanaged model has no table Django owns; dropping one
        would remove something created outside the migration graph."""
        unmanaged = {
            model._meta.db_table
            for model in apps.get_models(include_auto_created=True)
            if not model._meta.managed
        }

        assert tenant_only_table_names() & unmanaged == set()
