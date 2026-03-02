from django.apps import AppConfig


class TenantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tenant"

    def ready(self):
        import tenant.signals  # noqa: F401
