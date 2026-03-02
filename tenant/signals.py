from __future__ import annotations

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from tenant.models import Tenant, TenantDomain


@receiver([post_save, post_delete], sender=TenantDomain)
def invalidate_domain_caches(sender, instance, **kwargs):
    """Clear tenant resolve + CSRF domain caches when a domain changes."""
    cache.delete(f"tenant_resolve:{instance.domain}")
    if hasattr(instance, "tenant"):
        cache.delete(f"tenant_domains:{instance.tenant.schema_name}")


@receiver(post_save, sender=Tenant)
def invalidate_tenant_caches(sender, instance, **kwargs):
    """Clear caches for all domains of a tenant when tenant config changes."""
    for domain in instance.domains.values_list("domain", flat=True):
        cache.delete(f"tenant_resolve:{domain}")
    cache.delete(f"tenant_domains:{instance.schema_name}")
