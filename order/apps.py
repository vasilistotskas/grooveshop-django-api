from django.apps import AppConfig


class OrderConfig(AppConfig):
    name = "order"

    def ready(self):
        import order.signals.handlers  # noqa
