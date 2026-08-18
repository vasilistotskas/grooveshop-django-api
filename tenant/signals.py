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
