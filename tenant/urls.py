from django.urls import path

from tenant.views import (
    internal_domains,
    merchant_legal_identity_view,
    my_memberships,
    tenant_resolve,
)

app_name = "tenant"

urlpatterns = [
    path("tenant/resolve", tenant_resolve, name="tenant-resolve"),
    # Public: the seller identity the storefront must publish
    # (ECD art. 5, N. 4919/2022 art. 22). Tenant-schema scoped, so
    # it lives here rather than on the public-schema resolve path.
    path(
        "tenant/legal-identity",
        merchant_legal_identity_view,
        name="tenant-legal-identity",
    ),
    # Internal-token-gated: consumed by the media-stream service to
    # refresh its domain allowlists (excluded from the OpenAPI schema).
    path(
        "tenant/internal/domains",
        internal_domains,
        name="tenant-internal-domains",
    ),
    # Authenticated: lists all tenants the caller has active membership
    # in. Returns membership role so the Nuxt admin UI can conditionally
    # render OWNER/ADMIN controls.
    path(
        "tenant/memberships/mine",
        my_memberships,
        name="tenant-memberships-mine",
    ),
]
