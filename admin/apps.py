from django.contrib.admin.apps import AdminConfig


class MyAdminConfig(AdminConfig):
    default_site = "admin.admin.MyAdminSite"

    def ready(self):
        super().ready()
        from admin.signals import _connect_dashboard_invalidation

        _connect_dashboard_invalidation()
