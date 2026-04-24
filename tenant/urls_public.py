"""URL conf that always runs in public schema context.

Mounted via PUBLIC_SCHEMA_URLCONF in settings.py.
Tenant admin endpoints run here (public schema only). Everything else
falls through to the main ROOT_URLCONF via ``core_urlpatterns``, which
already includes ``tenant.urls`` — so ``tenant-resolve`` and
``tenant-memberships-mine`` are reachable both directly (via the
tenant app's URL conf) and through this public conf.

NOTE: We import ``urlpatterns`` from ``core.urls`` and extend the list
instead of using ``include("core.urls")`` because ``core.urls`` uses
``i18n_patterns()`` at module level, and Django forbids
``i18n_patterns`` inside an ``include()``.
"""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.urls import urlpatterns as core_urlpatterns
from tenant.views import TenantAdminViewSet

router = DefaultRouter()
router.register(
    r"api/v1/tenant/admin",
    TenantAdminViewSet,
    basename="tenant-admin",
)

urlpatterns = [
    # TenantAdminViewSet is public-schema only (guarded at the view
    # level too) and has no tenant.urls equivalent — register it here.
    # ``tenant/resolve`` and ``tenant/memberships/mine`` come through
    # ``core_urlpatterns`` below via ``include("tenant.urls")``; no need
    # to duplicate them here.
    path("", include(router.urls)),
] + core_urlpatterns
