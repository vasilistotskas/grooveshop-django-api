from django.urls import path

from tenant.views import internal_domains, my_memberships, tenant_resolve

app_name = "tenant"

urlpatterns = [
    path("tenant/resolve", tenant_resolve, name="tenant-resolve"),
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
