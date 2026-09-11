"""Build the ``TenantContext`` a strategy consults before answering.

Everything here is a cheap ``EXISTS`` or ``COUNT`` on the tenant's own
schema, computed once per request and cached for five minutes under
the default cache — whose ``KEY_FUNCTION`` scopes every key by schema,
so tenant A's counts can never answer for tenant B.

Strategies read the context; they never run these queries themselves.
That keeps "can this strategy answer well here?" a single, testable
decision per strategy instead of a scatter of ad-hoc lookups.
"""

from __future__ import annotations

from dataclasses import asdict

from django.conf import settings
from django.core.cache import cache
from django.db.models import Count

from recommendation.strategies.base import TenantContext
from tenant.models import TenantPlan

_CACHE_KEY = "recs:tenant_context"
_CACHE_TTL = 300


def _current_plan() -> str:
    from tenant.membership import get_current_tenant, resolve_current_tenant

    # Public schema — the platform operator, never plan-gated; also
    # never serves a storefront, so the value is academic there.
    if get_current_tenant() is None:
        return TenantPlan.ENTERPRISE
    # Unlike the boolean feature flags this cannot fail open: the Free
    # tier is the honest answer for a schema whose row cannot be read.
    tenant = resolve_current_tenant()
    return str(tenant.plan) if tenant is not None else TenantPlan.TRIAL


def current_vertical() -> str:
    """The active store's ``StoreVertical`` — what its presets key on.

    ``general`` on the public schema (nothing to reset there) and for a
    schema without a row; otherwise the real ``Tenant`` row's value,
    resolved even under a bare ``FakeTenant``.
    """
    from tenant.membership import resolve_current_tenant
    from tenant.models import StoreVertical

    tenant = resolve_current_tenant()
    return str(tenant.vertical) if tenant is not None else StoreVertical.GENERAL


def _semantic_available() -> bool:
    # Wired in Step 3 together with the embedder: until an index carries
    # one, the ``semantic`` strategy has nothing to query. Kept as a
    # context fact rather than a strategy-local check so the admin can
    # show WHY a configured strategy is being skipped.
    if settings.MEILISEARCH.get("OFFLINE"):
        return False
    return bool(getattr(settings, "RECOMMENDATION_EMBEDDER", None))


def build_tenant_context() -> TenantContext:
    """Uncached build. Prefer ``tenant_context()``."""
    from order.models.order import Order
    from product.models import Brand, Product, ProductAttribute
    from tag.models.tagged_item import TaggedItem

    # Joined rather than ``ContentType.objects.get_for_model``: django-
    # tenants clears the content-type cache on every schema switch, so
    # that lookup is a query or not depending on what ran before it.
    # One query here, always.
    product_opts = Product._meta

    return TenantContext(
        plan=_current_plan(),
        product_count=Product.objects.active().count(),
        has_brands=Brand.objects.exists(),
        has_tags=TaggedItem.objects.filter(
            content_type__app_label=product_opts.app_label,
            content_type__model=product_opts.model_name,
        ).exists(),
        has_attributes=ProductAttribute.objects.exists(),
        order_count=Order.objects.count(),
        multi_item_order_count=(
            Order.objects.annotate(n=Count("items")).filter(n__gte=2).count()
        ),
        semantic_available=_semantic_available(),
    )


def tenant_context() -> TenantContext:
    cached = cache.get(_CACHE_KEY)
    if cached is not None:
        return TenantContext(**cached)
    ctx = build_tenant_context()
    cache.set(_CACHE_KEY, asdict(ctx), _CACHE_TTL)
    return ctx


def invalidate_tenant_context() -> None:
    cache.delete(_CACHE_KEY)
