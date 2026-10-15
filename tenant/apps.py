from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TenantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tenant"
    verbose_name = _("Stores")

    def ready(self):
        from django.apps import apps
        from django.db.models.signals import post_migrate

        import tenant.signals  # noqa: F401
        from tenant.settings_metadata import sync_setting_metadata

        # After every migrate of a schema, the setting rows describe and
        # validate the settings as settings.py defines them now.
        post_migrate.connect(
            sync_setting_metadata,
            sender=apps.get_app_config("extra_settings"),
            dispatch_uid="tenant.sync_setting_metadata",
        )
