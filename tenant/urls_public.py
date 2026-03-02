"""URL conf that always runs in public schema context.

Mounted via PUBLIC_SCHEMA_URLCONF in settings.py.
Tenant resolve and admin endpoints run here (public schema only).
All other URLs fall through to the main ROOT_URLCONF.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from tenant.views import TenantAdminViewSet, tenant_resolve

router = DefaultRouter()
router.register(
    r"api/v1/tenant/admin",
    TenantAdminViewSet,
    basename="tenant-admin",
)

urlpatterns = [
    path("api/v1/tenant/resolve", tenant_resolve, name="tenant-resolve"),
    path("", include(router.urls)),
    # Fall through to main URL conf for everything else
    path("", include("core.urls")),
]
