from __future__ import annotations

import logging
from secrets import compare_digest

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import status, viewsets
from rest_framework.decorators import (
    api_view,
    permission_classes,
)
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from tenant.membership import get_current_tenant
from tenant.models import Tenant, TenantDomain, UserTenantMembership
from tenant.serializers import (
    TenantAdminSerializer,
    TenantConfigSerializer,
)

logger = logging.getLogger(__name__)

TENANT_RESOLVE_CACHE_TTL = 3600  # 1 hour


def _is_gateway(request: Request) -> bool:
    """True when the caller proves it is the agent gateway via the
    shared internal secret (the same one the order-event push uses).
    """
    secret = settings.AGENT_GATEWAY_INTERNAL_SECRET
    token = request.headers.get("X-Internal-Token", "")
    return bool(secret) and compare_digest(token, secret)


@extend_schema(
    responses=TenantConfigSerializer,
    parameters=[
        OpenApiParameter(
            name="domain",
            type=str,
            location=OpenApiParameter.QUERY,
            required=True,
        ),
    ],
    tags=["Tenant"],
)
@api_view(["GET"])
@permission_classes([AllowAny])
def tenant_resolve(request: Request) -> Response:
    domain = request.query_params.get("domain", "")
    if not domain:
        return Response(
            {"detail": "domain query parameter is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # "global:" — schema-independent (tenant.cache.make_tenant_key):
    # this endpoint can be called via ANY tenant's domain to resolve a
    # DIFFERENT domain, while tenant/signals.py's invalidation always
    # fires from the public schema. A schema-prefixed key here would
    # almost never match that invalidation.
    cache_key = f"global:tenant_resolve:{domain}"
    data = cache.get(cache_key)
    if data is None:
        # Always query from public schema
        tenant_domain = (
            TenantDomain.objects.select_related("tenant")
            .filter(domain=domain, tenant__is_active=True)
            .first()
        )

        if tenant_domain is None:
            return Response(
                {"detail": "Store not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        data = TenantConfigSerializer(tenant_domain.tenant).data
        cache.set(cache_key, data, TENANT_RESOLVE_CACHE_TTL)

    # Secrets ride only on internally-authenticated responses and are
    # never cached — the cache holds exactly the public payload.
    if _is_gateway(request):
        secrets = (
            TenantDomain.objects.filter(domain=domain, tenant__is_active=True)
            .values_list("tenant__chat_api_key", "tenant__acp_bearer_token")
            .first()
        )
        chat_api_key, acp_bearer_token = secrets or ("", "")
        data = {
            **data,
            "chat_api_key": chat_api_key or "",
            "acp_bearer_token": acp_bearer_token or "",
        }
    return Response(data)


@extend_schema(exclude=True)
@api_view(["GET"])
@permission_classes([AllowAny])
def internal_domains(request: Request) -> Response:
    """All active tenants' domains — for internal services only.

    The media-stream service refreshes its CORS / upstream-validation
    allowlists from this payload instead of a frozen env var, so
    onboarding a tenant domain needs no media-stream restart. Gated by
    the internal-services token (same header contract as the gateway's
    tenant-resolve secret channel); anonymous callers get 404 so the
    endpoint's existence is not advertised.

    Payload: registered TenantDomain rows of active, non-suspended
    tenants PLUS the derived ``api.`` / ``assets.`` / ``static.``
    service subdomains of each primary domain (the infra TEMPLATE
    provisions those for every tenant).
    """
    if not _is_gateway(request):
        from django.http import Http404

        raise Http404

    from tenant.internal import build_domains_payload  # noqa: PLC0415

    return Response(build_domains_payload())


@extend_schema(
    responses={
        200: {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "schemaName": {"type": "string"},
                    "name": {"type": "string"},
                    "storeName": {"type": "string"},
                    "primaryDomain": {"type": "string"},
                    "role": {
                        "type": "string",
                        "enum": ["member", "staff", "admin", "owner"],
                    },
                },
            },
        },
    },
    tags=["Tenant"],
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_memberships(request: Request) -> Response:
    """List the tenants the authenticated STAFF user has access to.

    Used to build the operator's tenant switcher and decide which admin
    links to render (OWNER/ADMIN/STAFF unlock different surfaces).

    Answers only in the public schema. Memberships are a platform-wide
    table keyed by a PUBLIC user id, while a request on a tenant host
    authenticates against that tenant's OWN user table — where the same
    id belongs to a different person. Filtering by that id would list
    stores the caller has nothing to do with. Customers hold no
    memberships at all (see ``tenant/membership.py``), so an empty list
    is the correct answer for every shopper.
    """
    if get_current_tenant() is not None:
        return Response([])

    # Prefetch ONLY the primary domain so the inline loop doesn't
    # re-query — ``.filter(is_primary=True).first()`` on a prefetched
    # related manager triggers a fresh query and discards the prefetch.
    memberships = (
        UserTenantMembership.objects.filter(
            user=request.user, is_active=True, tenant__is_active=True
        )
        .select_related("tenant")
        .prefetch_related(
            Prefetch(
                "tenant__domains",
                queryset=TenantDomain.objects.filter(is_primary=True),
                to_attr="_primary_domains",
            )
        )
    )

    out = []
    for m in memberships:
        primary_domains = getattr(m.tenant, "_primary_domains", [])
        primary = primary_domains[0] if primary_domains else None
        out.append(
            {
                "schemaName": m.tenant.schema_name,
                "name": m.tenant.name,
                "storeName": m.tenant.store_name or m.tenant.name,
                "primaryDomain": primary.domain if primary else "",
                "role": m.role,
            }
        )
    return Response(out)


class IsPlatformOperator(IsAdminUser):
    """Superuser, authenticated through the platform staff backend.

    ``TenantAdminSerializer`` returns EVERY tenant's Stripe secret key,
    Viva and ACS credentials, BoxNow secrets, Meta CAPI token and the
    chat/ACP bearer tokens. ``IsAdminUser`` alone gated that on
    ``is_staff``, which is:

    - ambiguous across schemas — ``is_staff`` is an attribute of
      whichever user table served the request, and pks are not unique
      between schemas; and
    - far too broad — onboarding a tenant requires giving its operator a
      public ``is_staff`` account (that is how ``PlatformStaffBackend``
      lets them into their own admin), and that same account could then
      read every OTHER merchant's payment credentials.

    Reading the whole platform's credential set is an owner operation,
    so require a superuser whose session was minted by
    ``PlatformStaffBackend`` — the same gate ``MyAdminSite`` uses, for
    the same pk-collision reason.
    """

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        if not getattr(request.user, "is_superuser", False):
            return False
        from tenant.auth_backends import (  # noqa: PLC0415
            is_platform_staff_session,
        )

        return is_platform_staff_session(request)


class TenantAdminViewSet(viewsets.ModelViewSet):
    queryset = Tenant.objects.all()
    serializer_class = TenantAdminSerializer
    permission_classes = [IsPlatformOperator]

    def _require_public_schema(self):
        if connection.schema_name != "public":
            # Returning 404 instead of 403 hides the endpoint's existence
            # from tenants that have no business knowing it's there
            # (H6 in MULTI_TENANT_AUDIT.md). A 403 leaks the URL surface
            # to anyone hitting the API on a tenant domain.
            from django.http import Http404

            raise Http404

    def get_queryset(self):
        if connection.schema_name != "public":
            return Tenant.objects.none()
        return super().get_queryset()

    def create(self, request, *args, **kwargs):
        self._require_public_schema()
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        self._require_public_schema()
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        self._require_public_schema()
        return super().destroy(request, *args, **kwargs)
