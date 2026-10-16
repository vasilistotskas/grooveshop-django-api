"""Deleting a platform identity from the public schema.

``UserAccount`` is in both ``SHARED_APPS`` and ``TENANT_APPS``: the
public copy holds platform identities, each store's copy its shoppers.
Its model still declares the reverse relations of every tenant app
(orders, carts, allauth email addresses, loyalty, …), and Django's
deletion collector follows all of them. In the public schema those
tables do not exist, so deleting a platform identity, or even opening
its delete confirmation, failed with ``relation "account_emailaddress"
does not exist`` (found on production, 2026-10-16).

Nothing in a tenant-only table can reference a PUBLIC identity: a
store's rows point at that store's own ``user_useraccount``. So in the
public schema those relations are skipped, and the shared ones
(memberships, sessions, the admin log, tokens, OTP devices) are deleted
as usual.

Deliberately scoped to identities. For a public model that stores do
reference across schemas (``Country``, ``Region``), skipping the tenant
tables would leave their rows pointing at nothing.
"""

from __future__ import annotations

from typing import Any

from django.contrib.admin.utils import NestedObjects, quote
from django.db import router
from django.db.models import QuerySet
from django.db.models.deletion import Collector
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html
from django.utils.text import capfirst

from tenant.app_labels import tenant_only_app_labels


class _SkipTenantOnlyRelations:
    """Collect nothing from apps whose tables exist only in tenant
    schemas."""

    def related_objects(self, related_model, related_fields, objs):
        if related_model._meta.app_label in set(tenant_only_app_labels()):
            return related_model._base_manager.using(self.using).none()
        return super().related_objects(related_model, related_fields, objs)


class PublicSchemaCollector(_SkipTenantOnlyRelations, Collector):
    pass


class PublicSchemaNestedObjects(_SkipTenantOnlyRelations, NestedObjects):
    pass


def delete_public(queryset: QuerySet[Any]) -> tuple[int, dict[str, int]]:
    """Delete these public-schema rows and what refers to them in the
    public schema."""
    collector = PublicSchemaCollector(
        using=router.db_for_write(queryset.model), origin=queryset
    )
    # QuerySet.delete hands its collector a QuerySet the same way; ty
    # cannot match the overloaded __getitem__ against the stub's
    # _IndexableCollection protocol.
    collector.collect(queryset)  # ty: ignore[invalid-argument-type]
    return collector.delete()


def public_deleted_objects(objs, request, admin_site):
    """``django.contrib.admin.utils.get_deleted_objects`` with the
    public-schema collector: the same nested list, counts, missing
    permissions and protected objects, for the confirmation page."""
    try:
        first = objs[0]
    except IndexError:
        return [], {}, set(), []
    collector = PublicSchemaNestedObjects(
        using=router.db_for_write(first._meta.model), origin=objs
    )
    collector.collect(objs)
    perms_needed = set()

    def format_callback(obj):
        opts = obj._meta
        no_edit_link = f"{capfirst(opts.verbose_name)}: {obj}"
        if not admin_site.is_registered(obj.__class__):
            return no_edit_link
        model_admin = admin_site.get_model_admin(obj.__class__)
        if not model_admin.has_delete_permission(request, obj):
            perms_needed.add(opts.verbose_name)
        try:
            admin_url = reverse(
                f"{admin_site.name}:{opts.app_label}_{opts.model_name}_change",
                None,
                (quote(obj.pk),),
            )
        except NoReverseMatch:
            return no_edit_link
        return format_html(
            '{}: <a href="{}">{}</a>',
            capfirst(opts.verbose_name),
            admin_url,
            obj,
        )

    to_delete = collector.nested(format_callback)
    protected = [format_callback(obj) for obj in collector.protected]
    model_count = {
        model._meta.verbose_name_plural: len(instances)
        for model, instances in collector.model_objs.items()
    }
    return to_delete, model_count, perms_needed, protected


class PublicSchemaDeletionMixin:
    """For a ModelAdmin of public-schema identities on the platform
    site: delete, and list what a delete removes, without touching
    tenant-only tables."""

    def delete_model(self, request, obj):
        delete_public(type(obj)._base_manager.filter(pk=obj.pk))

    def delete_queryset(self, request, queryset):
        delete_public(queryset)

    def get_deleted_objects(self, objs, request):
        return public_deleted_objects(objs, request, self.admin_site)
