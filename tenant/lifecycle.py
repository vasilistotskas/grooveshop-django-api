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
    return True


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
