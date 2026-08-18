from django.contrib.admin.apps import AdminConfig


class MyAdminConfig(AdminConfig):
    default_site = "admin.admin.MyAdminSite"

    def ready(self):
        super().ready()
        from admin.signals import (
            _connect_dashboard_invalidation,
            _connect_tenant_aware_last_login,
        )
        from admin.third_party import patch_djstripe_search_fields

        _connect_dashboard_invalidation()
        _connect_tenant_aware_last_login()
        patch_djstripe_search_fields()
