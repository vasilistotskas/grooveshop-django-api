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
        import logging  # noqa: PLC0415

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
