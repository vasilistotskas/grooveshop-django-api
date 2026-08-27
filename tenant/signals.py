from __future__ import annotations

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from tenant.models import Tenant, TenantDomain


@receiver([post_save, post_delete], sender=TenantDomain)
def invalidate_domain_caches(sender, instance, **kwargs):
    """Clear tenant resolve + CSRF domain caches when a domain changes.

    Admin/API contexts that mutate ``TenantDomain`` are gated to the
    public schema, so these deletes must target the same
    schema-independent "global:" keys the writers use (see
    ``tenant.cache.make_tenant_key`` / ``tenant/views.py`` /
    ``tenant/middleware.py``) — a schema-prefixed delete here would
    almost never match a write that happened on a tenant's own domain.

    Every SIBLING domain's cached payload must go too, not just the
    changed row's own: the resolve config embeds cross-row derivations
    (``apiDomain``/``assetsDomain``/``staticDomain`` prefer an explicit
    prefixed sibling row), so adding e.g. ``assets-staging.…`` changes
    the payload cached under the PRIMARY domain's key (observed on
    staging 2026-08-19: images kept pointing at the derived dot-host
    for the full TTL).
    """
    cache.delete(f"global:tenant_resolve:{instance.domain}")
    if hasattr(instance, "tenant"):
        for sibling in instance.tenant.domains.values_list("domain", flat=True):
            cache.delete(f"global:tenant_resolve:{sibling}")
        cache.delete(f"global:tenant_domains:{instance.tenant.schema_name}")


@receiver(post_save, sender=Tenant)
def invalidate_tenant_caches(sender, instance, **kwargs):
    """Clear caches for all domains of a tenant when tenant config changes."""
    for domain in instance.domains.values_list("domain", flat=True):
        cache.delete(f"global:tenant_resolve:{domain}")
    cache.delete(f"global:tenant_domains:{instance.schema_name}")


def _purge_resolve_for_current_schema():
    """Purge the tenant-resolve cache for the schema handling this write.

    For rows that live in a TENANT schema and are folded into the cached
    ``TenantConfigSerializer`` payload. The connection's current schema
    identifies whose domains to purge; the keys themselves are
    schema-independent "global:" keys (see ``invalidate_domain_caches``).
    """
    from django.db import connection  # noqa: PLC0415
    from django_tenants.utils import schema_context  # noqa: PLC0415

    schema = connection.schema_name
    if schema == "public":
        return
    with schema_context("public"):
        tenant = Tenant.objects.filter(schema_name=schema).first()
        if tenant is None:
            return
        for domain in tenant.domains.values_list("domain", flat=True):
            cache.delete(f"global:tenant_resolve:{domain}")


@receiver(post_save, sender="extra_settings.Setting")
def invalidate_resolve_on_agent_setting_change(sender, instance, **kwargs):
    """Purge the tenant-resolve cache when a merchant edits a setting
    that is FOLDED into the cached TenantConfig payload.

    ``AGENT_COMMERCE_ENABLED`` / ``PRODUCT_FEEDS_ENABLED`` are combined
    with the plan flag inside ``TenantConfigSerializer`` — without this
    purge a merchant toggle would sit behind the resolve cache for the
    full TTL. Setting rows live in the TENANT schema, so the current
    connection schema identifies whose domains to purge.
    """
    if getattr(instance, "name", "") not in {
        "AGENT_COMMERCE_ENABLED",
        "PRODUCT_FEEDS_ENABLED",
    }:
        return
    _purge_resolve_for_current_schema()


@receiver([post_save, post_delete], sender="pay_way.PayWay")
def invalidate_resolve_on_pay_way_change(sender, instance, **kwargs):
    """Purge the tenant-resolve cache when a pay-way changes.

    ``agent_payment_instruments`` is derived from the tenant's active
    offline pay-ways inside ``TenantConfigSerializer``, so a merchant
    enabling cash-on-delivery would otherwise stay invisible to AI
    agents for the full TTL — the agent gateway reads that list to
    decide which UCP payment instruments the store advertises.
    """
    _purge_resolve_for_current_schema()


@receiver(post_save, sender=Tenant)
def reactivate_on_renewal(sender, instance, **kwargs):
    """Recording a payment lifts a BILLING suspension immediately.

    An operator who moves ``paid_until`` to today or later on a store
    the dunning task suspended should not have to remember a second
    step. Scoped strictly to ``suspended_reason == BILLING`` — a manual
    (abuse/legal) suspension is never lifted by bookkeeping.

    Recursion-safe: ``activate_tenant`` saves the row, which re-enters
    this receiver with ``is_active=True`` and exits on the first guard.
    The dunning task's own suspension save also lands here, but at that
    moment ``paid_until`` is necessarily in the past, so nothing fires.
    """
    from django.utils import timezone  # noqa: PLC0415

    from tenant.lifecycle import activate_tenant  # noqa: PLC0415
    from tenant.models import SuspendedReason  # noqa: PLC0415

    if instance.is_active:
        return
    if instance.suspended_reason != SuspendedReason.BILLING:
        return
    if instance.paid_until is None:
        return
    if instance.paid_until < timezone.localdate():
        return
    activate_tenant(instance)
