from django.urls import path

from tenant.views import tenant_resolve

urlpatterns = [
    path("tenant/resolve", tenant_resolve, name="tenant-resolve"),
]
