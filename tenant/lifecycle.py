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

Resolve-cache invalidation needs nothing here: the ``post_save`` signal
on ``Tenant`` (tenant/signals.py) already clears every domain's cached
payload on any save.
"""

from __future__ import annotations

import logging

from django.utils import timezone

# Schemas that can never be suspended, activated, or destroyed through
# admin actions or automation. Destroying these breaks the platform.
PROTECTED_SCHEMAS = frozenset({"public", "webside"})


def suspend_tenant(tenant, *, reason: str) -> bool:
    """Suspend one tenant; True if this call changed its state.

    A no-op (False) for protected schemas and for tenants that are
    already fully suspended — the latter keeps both the destroy
    cooldown and the original ``suspended_reason`` intact, so the
    billing task can never relabel an operator's abuse suspension.
    """
    if tenant.schema_name in PROTECTED_SCHEMAS:
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

    # Drop the tenant's processed images from the media-stream cache so a
    # suspended store stops serving assets (they would otherwise persist
    # for the cache TTL — up to 180/360 days). Best-effort and off the
    # critical path: a broker or media-stream outage must never block the
    # suspension itself.
    _dispatch_media_flush(tenant.schema_name)
    return True


def _dispatch_media_flush(schema_name: str) -> None:
    try:
        from tenant.tasks import flush_tenant_media_task  # noqa: PLC0415

        flush_tenant_media_task.delay(schema_name)
    except Exception:  # noqa: BLE001 — never fail a suspend on dispatch
        logging.getLogger(__name__).warning(
            "could not dispatch media flush for %s", schema_name
        )


def activate_tenant(tenant) -> bool:
    """Reactivate one tenant; True if this call changed its state.

    Clears ``suspended_at`` (the destroy cooldown anchor) and
    ``suspended_reason`` together — an active tenant carries neither.
    """
    if tenant.schema_name in PROTECTED_SCHEMAS:
        return False

    tenant.is_active = True
    tenant.suspended_at = None
    tenant.suspended_reason = ""
    tenant.save(update_fields=["is_active", "suspended_at", "suspended_reason"])
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
    import os  # noqa: PLC0415

    from django.core.management import call_command  # noqa: PLC0415
    from django.utils import timezone  # noqa: PLC0415
    from django_tenants.utils import schema_context  # noqa: PLC0415

    from tenant.offboarding import private_media_root  # noqa: PLC0415

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
    import os  # noqa: PLC0415

    from tenant.offboarding import private_media_root  # noqa: PLC0415

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

    Callers are responsible for their own authorisation and safety
    gates (protected schema, suspended, cooldown); this function does
    the work once those pass, and refuses protected schemas itself as a
    last line of defence.
    """
    from django.utils import timezone  # noqa: PLC0415

    from tenant import offboarding  # noqa: PLC0415
    from tenant.models import TenantArchive  # noqa: PLC0415

    if tenant.schema_name in PROTECTED_SCHEMAS:
        raise ValueError(
            f"Refusing to destroy protected schema {tenant.schema_name!r}."
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
                offboarding.INVOICE_RETENTION_BASIS if retention_date else ""
            ),
            "purged_at": None,
        },
    )

    tenant.delete(force_drop=True)

    indexes = offboarding.purge_search_indexes(schema_name)
    files = offboarding.purge_tenant_files(schema_name)
    _dispatch_media_flush(schema_name)

    return {
        "schema_name": schema_name,
        "archive_id": archive.pk,
        "indexes_dropped": indexes,
        "files": files,
        "retention_until": retention_date,
    }
