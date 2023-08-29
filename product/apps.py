from django.apps import AppConfig


class ProductConfig(AppConfig):
    name = "product"

    def ready(self):
        from . import signals  # noqa
