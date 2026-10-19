from __future__ import annotations

from corsheaders.signals import check_request_enabled
from django.conf import settings
from django.db import connection
from django.db.models import ProtectedError
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver

from country.models import Country
from region.models import Region
from tenant.cache import bump_cache_generation
from tenant.features import (
    feature_plan_flags,
    feature_setting_gates,
    feature_setting_keys,
)
from tenant.middleware import origin_belongs_to_tenant
from tenant.models import Tenant, TenantDomain
from tenant.reference_guard import references_to
from tenant.switches import on_switch_change

# Every page the storefront has cached for a store shows what the store
# offers (navigation, footer, product cards), so a feature switch purges
# them all. The storefront reads the plan flags from its copy of the
# resolve payload, so that purge waits until the copy has expired.
on_switch_change(
    setting_gates=feature_setting_gates(),
    plan_flags=feature_plan_flags(),
    task="core.tasks.purge_store_pages_task",
    plan_countdown=settings.STOREFRONT_TENANT_CACHE_SECONDS,
)

# The language switcher, hreflang set and sitemap of every page follow
# ``available_locales``. The storefront reads it from its copy of the
# resolve payload, so the purge waits until that copy has expired.
on_switch_change(
    setting_gates=(),
    plan_flags=("available_locales",),
    task="core.tasks.purge_store_locales_task",
    plan_countdown=settings.STOREFRONT_TENANT_CACHE_SECONDS,
)


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
    with the plan flag inside ``TenantConfigSerializer``, and so is every
    feature switch in ``TenantConfig.features`` (``tenant/features.py``)
    — without this bump a merchant toggle would sit behind the resolve
    cache for the full TTL. Setting rows live in the TENANT schema, so
    the current connection schema identifies whose generation to bump.
    """
    if getattr(instance, "name", "") not in {
        "AGENT_COMMERCE_ENABLED",
        "PRODUCT_FEEDS_ENABLED",
        "AGENT_HOSTED_PAYMENT_ENABLED",
        # Folded into ``TenantConfig.features``.
        *feature_setting_keys(),
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


@receiver(
    [post_save, post_delete],
    sender="shipping.ShippingProvider",
    dispatch_uid="tenant.bump_generation_on_shipping_provider_change",
)
def bump_generation_on_shipping_provider_change(sender, instance, **kwargs):
    """Move the tenant-resolve cache on when a carrier changes.

    ``shippingCarriers`` is the tenant's active carriers, so switching
    one on or off, or renaming it, must not sit behind the resolve cache
    for the full TTL.
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


@receiver(
    pre_delete,
    sender=Country,
    dispatch_uid="tenant.refuse_delete_country_in_use",
)
@receiver(
    pre_delete,
    sender=Region,
    dispatch_uid="tenant.refuse_delete_region_in_use",
)
def refuse_delete_of_reference_data_in_use(sender, instance, **kwargs):
    """Never delete a country or region a store's rows still point at.

    The admin refuses first, on its confirmation page, naming the stores
    (``tenant.deletion.PublicReferenceDataDeletionMixin``). This is the
    backstop for every other path - the shell, a management command, a
    delete issued from inside one store's schema, which would null that
    store's orders and addresses while another store's rows still
    reference the row.

    Raises inside ``Collector.delete``'s transaction, so nothing is
    removed.
    """
    in_use = references_to([instance])
    if in_use:
        raise ProtectedError(
            "{} is still used: {}".format(
                instance, "; ".join(str(refs) for refs in in_use)
            ),
            set(),
        )
