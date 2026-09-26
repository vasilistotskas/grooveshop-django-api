from __future__ import annotations

from corsheaders.signals import check_request_enabled
from django.db import connection
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from tenant.cache import bump_cache_generation
from tenant.middleware import origin_belongs_to_tenant
from tenant.models import Tenant, TenantDomain


@receiver(check_request_enabled, dispatch_uid="tenant.allow_tenant_origin")
def allow_tenant_origin(sender, request, **kwargs) -> bool:
    """CORS allow for the CURRENT tenant's own domains.

    ``CORS_ALLOWED_ORIGINS`` holds the platform origins; every tenant
    storefront is a distinct origin that only its ``TenantDomain`` rows
    know, so the static list cannot cover them and an allow-all would
    hand credentialed CORS to the whole internet. django-cors-headers
    echoes the Origin (with credentials) when a receiver returns True.
    ``CorsMiddleware`` sits after ``TenantMainMiddleware``, so the tenant
    is bound; ``connection.tenant`` rather than ``get_current_tenant``
    so the platform console's own domain rows count on the public
    schema too. Server-to-server callers (Nuxt SSR, the agent gateway,
    media-stream) send no Origin and never reach this.
    """
    origin = request.headers.get("Origin", "")
    if not origin:
        return False
    return origin_belongs_to_tenant(getattr(connection, "tenant", None), origin)


@receiver(
    [post_save, post_delete],
    sender=TenantDomain,
    dispatch_uid="tenant.bump_generation_on_domain_change",
)
def bump_generation_on_domain_change(sender, instance, **kwargs):
    """A domain row changed: move the tenant's cached entries on.

    Both families read it — ``tenant_domains`` is the list itself, and
    the resolve payload embeds cross-row derivations
    (``apiDomain``/``assetsDomain``/``staticDomain`` prefer an explicit
    prefixed sibling row), so adding e.g. ``assets-staging.…`` changes
    what the PRIMARY domain resolves to (observed on staging
    2026-08-19). One generation covers every sibling at once.

    ``tenant_id``, not ``tenant``: on a cascade the parent row may be
    going too, and the UPDATE then simply matches nothing.
    """
    bump_cache_generation(pk=instance.tenant_id)


def _bump_generation_for_current_schema() -> None:
    """Bump the generation of the tenant whose schema this write is in.

    For rows that live in a TENANT schema and are folded into the cached
    ``TenantConfigSerializer`` payload. The public schema owns no
    storefront's settings or pay-ways, so a write there (seed and
    fixture loads) moves nothing. ``Tenant`` is a shared model: the
    tenant schema's ``search_path`` ends in ``public``, so the UPDATE
    reaches it without leaving the caller's transaction.
    """
    schema = connection.schema_name
    if schema == "public":
        return
    bump_cache_generation(schema_name=schema)


@receiver(
    post_save,
    sender="extra_settings.Setting",
    dispatch_uid="tenant.bump_generation_on_agent_setting_change",
)
def bump_generation_on_agent_setting_change(sender, instance, **kwargs):
    """Move the tenant-resolve cache on when a merchant edits a setting
    that is FOLDED into the cached TenantConfig payload.

    ``AGENT_COMMERCE_ENABLED`` / ``PRODUCT_FEEDS_ENABLED`` are combined
    with the plan flag inside ``TenantConfigSerializer`` — without this
    bump a merchant toggle would sit behind the resolve cache for the
    full TTL. Setting rows live in the TENANT schema, so the current
    connection schema identifies whose generation to bump.
    """
    if getattr(instance, "name", "") not in {
        "AGENT_COMMERCE_ENABLED",
        "PRODUCT_FEEDS_ENABLED",
        "AGENT_HOSTED_PAYMENT_ENABLED",
    }:
        return
    _bump_generation_for_current_schema()


@receiver(
    [post_save, post_delete],
    sender="pay_way.PayWay",
    dispatch_uid="tenant.bump_generation_on_pay_way_change",
)
def bump_generation_on_pay_way_change(sender, instance, **kwargs):
    """Move the tenant-resolve cache on when a pay-way changes.

    ``agent_payment_instruments`` is derived from the tenant's active
    offline pay-ways inside ``TenantConfigSerializer``, so a merchant
    enabling cash-on-delivery would otherwise stay invisible to AI
    agents for the full TTL — the agent gateway reads that list to
    decide which UCP payment instruments the store advertises.
    """
    _bump_generation_for_current_schema()


@receiver(post_save, sender=Tenant, dispatch_uid="tenant.reactivate_on_renewal")
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
    from django.utils import timezone

    from tenant.lifecycle import activate_tenant
    from tenant.models import SuspendedReason

    if instance.is_active:
        return
    if instance.suspended_reason != SuspendedReason.BILLING:
        return
    if instance.paid_until is None:
        return
    if instance.paid_until < timezone.localdate():
        return
    activate_tenant(instance)
