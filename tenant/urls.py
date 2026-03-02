from django.urls import path

from tenant.views import tenant_resolve

app_name = "tenant"

urlpatterns = [
    path("tenant/resolve", tenant_resolve, name="tenant-resolve"),
]
