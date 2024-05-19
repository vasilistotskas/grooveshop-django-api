from django.apps import AppConfig


class ContactConfig(AppConfig):
    name = "contact"

    def ready(self):
        from . import signals  # noqa
