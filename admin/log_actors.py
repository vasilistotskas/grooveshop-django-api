"""Resolve ``django_admin_log`` actors across the schema boundary.

An admin-log row is the one record in this system with MIXED
provenance: a PUBLIC-schema actor acting on a TENANT-schema object.
Its two foreign keys therefore pull in opposite directions and
PostgreSQL cannot express a cross-schema FK, so exactly one of them
can be real:

``content_type_id``
    Must resolve in the TENANT schema. A tenant's app set differs from
    public's, so the id spaces differ — verified in production, where
    webside's ``product.product`` content type is
    ``notification.notificationuser`` in public. This is why
    ``django.contrib.admin`` is dual-listed into ``TENANT_APPS``
    (see the comment on that entry in ``settings.py``).

``user_id``
    Is necessarily a PUBLIC pk: on a tenant host every admin session is
    a platform identity by construction (``PlatformStaffBackend``, and
    ``MyAdminSite.has_permission`` refuses anything else). It cannot be
    resolved against the tenant's own ``user_useraccount``.

So ``content_type_id`` keeps its constraint and ``user_id`` loses it —
the same trade ``product.changed_by``, ``order.stock_log`` and the
ACS/BoxNow shipment histories already make with ``db_constraint=False``.
The constraint itself is dropped per tenant schema by
``user/migrations/0026_admin_log_public_actor_fk``; public keeps a real
FK because there the actor genuinely IS local.

Dropping the constraint alone would only trade a crash for a lie:
``LogEntry.user`` would still be resolved against the tenant's user
table, yielding a blank actor when no such pk exists there (Django
templates swallow ``ObjectDoesNotExist``) or — once the tenant has
grown past that pk — the name of an unrelated shopper. This module
closes that half by binding the PUBLIC identity onto the instances
before they are rendered.
"""

from __future__ import annotations

from typing import Iterable

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django_tenants.utils import get_public_schema_name, schema_context


class UnknownActor:
    """Stand-in for an actor that no longer exists in the public schema.

    Caching ``None`` would NOT do: ``ForwardManyToOneDescriptor`` treats
    a cached ``None`` alongside a non-null ``user_id`` as a cache miss
    and falls back to a database lookup — against the TENANT's user
    table, which is precisely the misattribution this module exists to
    prevent. A non-``None`` sentinel is what actually stops that query.

    Deliberately not a ``UserAccount`` instance: it must be impossible
    to mistake for a real account, and fabricating an identity for an
    actor we cannot identify would be worse than admitting we cannot.
    It renders as empty in ``object_history.html`` while keeping the pk
    available for anyone debugging the row.
    """

    __slots__ = ("pk",)

    def __init__(self, pk: object) -> None:
        self.pk = pk

    def get_username(self) -> str:
        return ""

    def get_full_name(self) -> str:
        return ""

    def __str__(self) -> str:
        return ""

    def __repr__(self) -> str:
        return f"<UnknownActor pk={self.pk!r}>"


def bind_public_actors(entries: Iterable[LogEntry]) -> list[LogEntry]:
    """Attach each entry's PUBLIC-schema actor to its ``user`` cache.

    Populating the forward-descriptor cache means templates and code
    reading ``entry.user`` get the platform identity without ever
    querying the tenant's ``user_useraccount`` — so no lookup can
    silently match an unrelated shopper who happens to share the pk.

    An actor that cannot be found in public binds to
    :class:`UnknownActor` for the reason documented there.
    """
    entries = list(entries)
    field = LogEntry._meta.get_field("user")
    actor_ids = {e.user_id for e in entries if e.user_id is not None}
    if not actor_ids:
        return entries

    with schema_context(get_public_schema_name()):
        actors = {
            user.pk: user
            for user in get_user_model()._default_manager.filter(
                pk__in=actor_ids
            )
        }

    for entry in entries:
        if entry.user_id is None:
            continue
        field.set_cached_value(
            entry,
            actors.get(entry.user_id) or UnknownActor(entry.user_id),
        )
    return entries


def patch_admin_history_actors() -> None:
    """Make every ModelAdmin's history page resolve actors from public.

    Applied once from ``MyAdminConfig.ready()`` rather than mixed into
    a base class: the admins that write log entries include ones this
    project does not own (dj-stripe, allauth, django-celery-beat,
    simple-history) and ones that extend unfold's ``ModelAdmin``
    directly rather than ``admin.base.BaseModelAdmin`` (``TenantAdmin``
    is both a direct subclass and an active ``log_change`` caller). A
    single seam covers all of them, present and future, with no
    per-admin adoption step to forget.

    ``history_view`` builds ``context["action_list"]`` as a paginated
    ``Page``; the entries hang off ``page.object_list``.
    """
    from django.contrib.admin.options import ModelAdmin

    if getattr(ModelAdmin.history_view, "_binds_public_actors", False):
        return

    original = ModelAdmin.history_view

    def history_view(self, request, object_id, extra_context=None):
        response = original(self, request, object_id, extra_context)
        context = getattr(response, "context_data", None)
        if context is None:
            # A redirect (missing object / no permission) has no context.
            return response
        page = context.get("action_list")
        object_list = getattr(page, "object_list", None)
        if object_list is not None:
            page.object_list = bind_public_actors(object_list)
        elif page is not None:
            context["action_list"] = bind_public_actors(page)
        return response

    history_view._binds_public_actors = True
    ModelAdmin.history_view = history_view
