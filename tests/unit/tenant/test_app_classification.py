"""The SHARED ∩ TENANT app set is load-bearing — pin it.

An app that (a) owns a table written during an ordinary request and
(b) is reachable on a TENANT host must live in BOTH ``SHARED_APPS`` and
``TENANT_APPS``, so the table exists per-schema. Miss one and its rows
fall through the search_path into ``public`` carrying ids resolved
against the tenant's own copy of a sibling table — the class of bug that
silently mislabels data and eventually trips a cross-schema FK.

``admin`` is the case this suite was added for: the merchant admin runs
on tenant hosts, so ``django_admin_log`` must be per-schema. It was
SHARED-only until the merchant-admin feature made it tenant-facing, so
every merchant admin action logged into ``public.django_admin_log`` with
a ``content_type_id`` from the tenant's ``django_content_type`` — a
different id space (observed in prod: webside ``product.product`` CT id
48 is ``notification.notificationuser`` in public).
"""

from __future__ import annotations

from django.conf import settings
from django_tenants.routers import TenantSyncRouter

# Every app that owns request-written tables AND is served on tenant
# hosts. Each MUST be dual-listed; a removal reintroduces a cross-schema
# fall-through bug. ``admin`` is the newest member (H1).
DUAL_LISTED_REQUIRED = {
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "user",
    "extra_settings",
    "allauth.usersessions",
    "admin.apps.MyAdminConfig",
}

# Apps whose tables deliberately exist in ``public`` ONLY, even though
# something in a tenant schema may write them. Falling through the
# search_path is the intent here, not the bug — see the class below for
# what has to be true before an app may be listed.
SHARED_ONLY_BY_DESIGN = {"django_celery_results"}


def _labels(app_entries):
    return {a.split(".")[-1] if "." in a else a for a in app_entries}


class TestDualListedApps:
    def test_every_request_written_shared_app_is_also_tenant(self):
        shared = set(settings.SHARED_APPS)
        tenant = set(settings.TENANT_APPS)
        for app in DUAL_LISTED_REQUIRED:
            assert app in shared, f"{app} dropped from SHARED_APPS"
            assert app in tenant, (
                f"{app} is not in TENANT_APPS — its table would exist "
                "only in public and tenant-host writes would fall "
                "through the search_path into the public copy"
            )

    def test_admin_is_dual_listed(self):
        assert "admin.apps.MyAdminConfig" in settings.SHARED_APPS
        assert "admin.apps.MyAdminConfig" in settings.TENANT_APPS


class TestSharedOnlyByDesign:
    """``django_celery_results`` is the deliberate exception to the rule
    above, and it only holds because the hazard that rule guards does
    not exist for it.

    That hazard is an id resolved in one schema landing in a row stored
    in another — ``django_admin_log`` writing a ``content_type_id`` from
    the tenant's ``django_content_type`` into ``public``. The celery
    result models carry NO relations at all: every column is a scalar
    (``content_type`` there is the result's encoding, "application/json",
    not a ContentType FK). A row is therefore self-contained and means
    the same thing in any schema.

    Public-only is also the requirement, not merely the tolerance: only
    failures are stored, the control plane reads every store's in one
    list, and ``celery.backend_cleanup`` is dispatched by beat with no
    tenant header — so it runs in ``public`` and could never prune a
    tenant-schema row.

    Before adding anything to ``SHARED_ONLY_BY_DESIGN``, the relation
    check below must hold for it too.
    """

    def test_listed_apps_are_shared_only(self):
        for app in SHARED_ONLY_BY_DESIGN:
            assert app in settings.SHARED_APPS, (
                f"{app} dropped from SHARED_APPS"
            )
            assert app not in settings.TENANT_APPS, (
                f"{app} is back in TENANT_APPS — its rows would be written "
                "into whichever tenant schema the task ran in, where the "
                "control plane cannot read them and backend_cleanup, which "
                "runs in public, will never prune them"
            )

    def test_listed_apps_own_no_cross_schema_relations(self):
        """The safety condition. A relation would make a row depend on
        an id space that differs per schema, which is exactly the bug
        ``DUAL_LISTED_REQUIRED`` exists to prevent."""
        from django.apps import apps as django_apps

        for app in SHARED_ONLY_BY_DESIGN:
            config = django_apps.get_app_config(app.split(".")[-1])
            for model in config.get_models():
                relations = [
                    field.name
                    for field in model._meta.get_fields()
                    if field.is_relation
                ]
                assert not relations, (
                    f"{model._meta.label} has relations {relations} but is "
                    "stored public-only — an id resolved in a tenant schema "
                    "would be written against the public id space"
                )


class TestAdminLogRoutesPerSchema:
    """The behavioural seam: django-tenants' OWN migration router must
    place ``admin`` (hence ``django_admin_log``) in both public and every
    tenant schema. This is what creates the per-schema table."""

    def setup_method(self):
        self.router = TenantSyncRouter()

    def test_admin_migrates_into_tenant_schemas(self):
        assert self.router.app_in_list("admin", settings.TENANT_APPS), (
            "admin would not migrate into tenant schemas — "
            "django_admin_log would be missing there"
        )

    def test_admin_still_migrates_into_public(self):
        # The PLATFORM admin runs on public and keeps its own log.
        assert self.router.app_in_list("admin", settings.SHARED_APPS)

    def test_logentry_content_type_fk_resolves_within_schema(self):
        """Both FK targets of LogEntry are per-schema, so a tenant's log
        row references that tenant's own content_type / user rows."""
        from django.apps import apps

        log_entry = apps.get_model("admin", "logentry")
        ct_field = log_entry._meta.get_field("content_type")
        user_field = log_entry._meta.get_field("user")
        # contenttypes and the user model are both dual-listed, so these
        # FKs land in the schema the LogEntry itself lives in.
        assert ct_field.related_model._meta.app_label == "contenttypes"
        assert self.router.app_in_list(
            ct_field.related_model._meta.app_label, settings.TENANT_APPS
        )
        assert self.router.app_in_list(
            user_field.related_model._meta.app_label, settings.TENANT_APPS
        )


class TestAdminNotExposedToMerchants:
    """Dual-listing must NOT turn admin's own models into a merchant
    admin surface — that gate is separate from table placement."""

    def test_admin_is_not_a_tenant_only_store_surface(self):
        from tenant.app_labels import tenant_only_app_labels

        # SHARED membership wins, so admin is never "tenant-only" (which
        # is what the store-scope permission set is derived from).
        assert "admin" not in tenant_only_app_labels()

    def test_admin_is_platform_only_for_role_grants(self):
        from tenant.role_scopes import PLATFORM_ONLY_APP_LABELS

        assert "admin" in PLATFORM_ONLY_APP_LABELS
