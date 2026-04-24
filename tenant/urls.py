from django.urls import path

from tenant.views import my_memberships, tenant_resolve

app_name = "tenant"

urlpatterns = [
    path("tenant/resolve", tenant_resolve, name="tenant-resolve"),
    # Authenticated: lists all tenants the caller has active membership
    # in. Returns membership role so the Nuxt admin UI can conditionally
    # render OWNER/ADMIN controls.
    path(
        "tenant/memberships/mine",
        my_memberships,
        name="tenant-memberships-mine",
    ),
]
