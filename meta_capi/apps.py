from django.apps import AppConfig


class MetaCapiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "meta_capi"
    verbose_name = "Meta Conversions API"

    def ready(self) -> None:
        from . import signals  # noqa: F401  — register receivers
