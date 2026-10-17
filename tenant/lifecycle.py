"""Suspend / activate a tenant — ONE code path for admin and automation.

The semantics used to live inline in the TenantAdmin bulk actions; the
billing dunning task (tenant/billing.py) needs the exact same rules, so
they are extracted here rather than duplicated:

- Protected schemas are never touched.
- ``suspended_at`` is stamped only on the FIRST suspension —
  re-suspending must not reset the 24h destroy cooldown.
- ``suspended_reason`` records who acted (operator vs billing task),
  which is what scopes renewal-driven auto-reactivation to billing
  suspensions only.

Resolve-cache invalidation needs nothing here: every ``Tenant.save``
bumps ``cache_generation`` in the same UPDATE, and the resolve and
domain cache keys include it (tenant/cache.py), so the next read after
the commit builds a fresh key.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from django.utils import timezone
from django_tenants.utils import get_public_schema_name

if TYPE_CHECKING:
    from core.cache.edge import MediaPurge

# Minimum time a tenant must be suspended before it can be destroyed:
# an accidental suspension followed by an immediate destroy is the
# irreversible mistake this window exists to prevent.
SUSPEND_COOLDOWN = timedelta(hours=24)

DESTROY_REFUSALS = {
    "protected": "it is a protected tenant",
    "not_suspended": "it must be suspended first",
    "cooldown": f"it was suspended less than {SUSPEND_COOLDOWN} ago",
}


def is_protected_tenant(tenant) -> bool:
    """True when *tenant* may never be suspended, activated or destroyed.

    The public schema IS the platform, so it is protected by
    construction. Every other protection is the ``Tenant.is_protected``
    row flag, set by a platform superuser in the admin — no store is
    named in code, and shielding a new merchant needs no deploy.
    """
    if tenant.schema_name == get_public_schema_name():
        return True
    return bool(getattr(tenant, "is_protected", False))


def destroy_refusal(tenant, *, now=None) -> str | None:
    """Why *tenant* cannot be destroyed right now, or ``None`` if it can.

    ONE gate list for the admin action, the platform API and any future
    script (keys of ``DESTROY_REFUSALS``): a protected tenant never; a
    live store must be suspended first; and ``SUSPEND_COOLDOWN`` must
    have elapsed since the suspension.
    """
    if is_protected_tenant(tenant):
        return "protected"
    if tenant.is_active or tenant.suspended_at is None:
        return "not_suspended"
    if (now or timezone.now()) - tenant.suspended_at < SUSPEND_COOLDOWN:
        return "cooldown"
    return None


def suspend_tenant(tenant, *, reason: str) -> bool:
    """Suspend one tenant; True if this call changed its state.

    A no-op (False) for protected schemas and for tenants that are
    already fully suspended — the latter keeps both the destroy
    cooldown and the original ``suspended_reason`` intact, so the
    billing task can never relabel an operator's abuse suspension.
    """
    if is_protected_tenant(tenant):
        return False
    if not tenant.is_active and tenant.suspended_at is not None:
        return False

    update_fields = ["is_active", "suspended_reason"]
    tenant.is_active = False
    tenant.suspended_reason = reason
    if tenant.suspended_at is None:
        tenant.suspended_at = timezone.now()
        update_fields.append("suspended_at")
    tenant.save(update_fields=update_fields)

    # Stop serving the store's images: park the originals out of the
    # served path first (tenant.media_parking), then drop the cached copies
    # from media-stream and the Cloudflare edge, which would otherwise
    # persist for the cache TTL — up to 180/360 days. In that order, a
    # request that refills a cache after the flush finds no original.
    # Best-effort and off the critical path: a disk, broker, media-stream
    # or Cloudflare failure must never block the suspension itself.
    _park_media(tenant.schema_name)
    _dispatch_media_flush(tenant.schema_name, _media_edge_purges(tenant))
    return True


def _park_media(schema_name: str) -> bool:
    try:
        from tenant.media_parking import park_tenant_media

        return park_tenant_media(schema_name)
    except OSError:
        logging.getLogger(__name__).exception(
            "could not park the media of %s; its images stay served",
            schema_name,
        )
        return False


def _media_edge_purges(tenant) -> list[MediaPurge]:
    """Where the tenant's images sit at the Cloudflare edge.

    Read from the tenant row, so a caller that deletes the row must call
    this first. Best-effort: an unreadable row only costs the edge purge.
    The reads run in a savepoint: ``destroy_tenant`` calls this inside its
    transaction, where a failed query left unrolled would abort the
    transaction and fail the destroy itself.
    """
    from django.db import transaction

    try:
        from core.cache import edge

        with transaction.atomic():
            return edge.tenant_media_purges(tenant)
    except Exception:
        logging.getLogger(__name__).warning(
            "could not plan the edge purge of %s's images",
            tenant.schema_name,
            exc_info=True,
        )
        return []


def _dispatch_media_flush(
    schema_name: str, edge_purges: list[MediaPurge] | None = None
) -> None:
    try:
        from tenant.tasks import flush_tenant_media_task

        flush_tenant_media_task.delay(schema_name, edge_purges or [])
    except Exception:
        logging.getLogger(__name__).warning(
            "could not dispatch media flush for %s", schema_name
        )


def activate_tenant(tenant) -> bool:
    """Reactivate one tenant; True if this call changed its state.

    Clears ``suspended_at`` (the destroy cooldown anchor) and
    ``suspended_reason`` together — an active tenant carries neither.
    """
    if is_protected_tenant(tenant):
        return False

    # Put the parked originals back before the store is active again. A
    # failed move raises (OSError) and leaves the store suspended, rather
    # than active with its images still parked and nothing to retry.
    from tenant.media_parking import restore_tenant_media

    restored = restore_tenant_media(tenant.schema_name)

    tenant.is_active = True
    tenant.suspended_at = None
    tenant.suspended_reason = ""
    tenant.save(update_fields=["is_active", "suspended_at", "suspended_reason"])

    # Drop what was cached while the originals were away: media-stream's
    # placeholders and 5-minute negative entries, and the edge's copies of
    # both. Nothing restored, nothing to drop.
    if restored:
        _dispatch_media_flush(tenant.schema_name, _media_edge_purges(tenant))
    return True


def export_tenant_data(tenant, *, actor: str = "") -> str:
    """Serialise a store's data so the controller can take it with them.

    GDPR art. 28(3)(g) obliges a processor to delete OR RETURN personal
    data at the END of processing, *at the controller's choice*. The
    platform is the processor here and the merchant is the controller,
    so offering only deletion answers half the article: destroying a
    store silently removed the merchant's own records with no way to
    take a copy first.

    Writes a JSON dump of the tenant schema under the private tree,
    which is the only volume both the backend and the celery worker
    mount. Returns the path.
    """
    import os

    from django.core.management import call_command
    from django.utils import timezone
    from django_tenants.utils import schema_context

    from tenant.offboarding import private_media_root

    stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
    export_dir = os.path.join(
        private_media_root(), "_tenant_exports", tenant.schema_name
    )
    os.makedirs(export_dir, exist_ok=True)
    path = os.path.join(export_dir, f"{tenant.schema_name}-{stamp}.json")

    with (
        schema_context(tenant.schema_name),
        open(path, "w", encoding="utf-8") as handle,
    ):
        # natural-foreign/-primary keep the dump portable: pk values are
        # meaningless once the schema is gone, so a re-import elsewhere
        # needs names rather than ids.
        call_command(
            "dumpdata",
            "--natural-foreign",
            "--natural-primary",
            "--exclude=contenttypes",
            "--exclude=auth.permission",
            "--exclude=sessions",
            indent=2,
            stdout=handle,
        )

    logging.getLogger(__name__).info(
        "Exported tenant %s to %s (requested by %s)",
        tenant.schema_name,
        path,
        actor or "unknown",
    )
    return path


def has_tenant_export(tenant) -> bool:
    """Whether a data export already exists for this store."""
    import os

    from tenant.offboarding import private_media_root

    export_dir = os.path.join(
        private_media_root(), "_tenant_exports", tenant.schema_name
    )
    return os.path.isdir(export_dir) and any(os.scandir(export_dir))


def destroy_tenant(tenant, *, actor: str = "") -> dict:
    """Destroy a tenant: erase what must go, retain what must stay.

    ONE path for every caller. The admin bulk action and the platform
    REST endpoint used to destroy tenants differently — the admin passed
    ``force_drop=True`` while the API called plain ``instance.delete()``,
    and since ``auto_drop_schema`` defaults to False that left the entire
    Postgres schema orphaned behind a deleted row. Two verbs spelled the
    same, doing materially different things.

    Order matters and is not arbitrary:

    1. Read the last invoice year FIRST — it lives inside the schema
       that step 3 destroys.
    2. Record the erasure (``TenantArchive``) BEFORE deleting, so a
       crash mid-way leaves evidence rather than a silent gap.
    3. Drop the schema and the row.
    4. Erase files and search indexes, which need only the schema NAME
       and so survive the row being gone.

    Invoices are deliberately NOT erased — see ``tenant.offboarding``.

    Authorisation is the caller's job; the safety gates are not.
    ``destroy_refusal`` runs HERE, so no caller — the admin action, the
    platform API or a future script — can skip the suspended-first and
    cooldown rules the way the API once did.
    """
    from django.db import transaction
    from django.utils import timezone

    from tenant import offboarding
    from tenant.models import Tenant, TenantArchive

    with transaction.atomic():
        # Judge the row as it is NOW, under lock — not the caller's
        # snapshot. Between an operator loading the changelist and
        # confirming, someone else may have re-activated or protected
        # the store; the gate must see that write, and the lock holds a
        # concurrent activate off until the drop has committed.
        tenant = Tenant.objects.select_for_update().get(pk=tenant.pk)
        refusal = destroy_refusal(tenant)
        if refusal is not None:
            raise ValueError(
                f"Refusing to destroy {tenant.schema_name!r}: "
                f"{DESTROY_REFUSALS[refusal]}."
            )

        schema_name = tenant.schema_name
        tenant_name = tenant.name

        invoice_year = offboarding.latest_invoice_year(schema_name)
        retention_date = (
            offboarding.retention_until(invoice_year)
            if invoice_year is not None
            else None
        )

        archive, _created = TenantArchive.objects.update_or_create(
            schema_name=schema_name,
            defaults={
                "tenant_name": tenant_name,
                "destroyed_at": timezone.now(),
                "destroyed_by": actor,
                "data_exported": has_tenant_export(tenant),
                "retained_invoice_path": (
                    offboarding.tenant_invoice_dir(schema_name)
                    if retention_date
                    else ""
                ),
                "retention_until": retention_date,
                "retention_basis": (
                    offboarding.INVOICE_RETENTION_BASIS
                    if retention_date
                    else ""
                ),
                "purged_at": None,
            },
        )

        # The domains and credentials the edge purge needs go with the row.
        edge_purges = _media_edge_purges(tenant)

        tenant.delete(force_drop=True)

    indexes = offboarding.purge_search_indexes(schema_name)
    files = offboarding.purge_tenant_files(schema_name)
    _dispatch_media_flush(schema_name, edge_purges)

    return {
        "schema_name": schema_name,
        "archive_id": archive.pk,
        "indexes_dropped": indexes,
        "files": files,
        "retention_until": retention_date,
    }
