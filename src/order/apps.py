from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OrderConfig(AppConfig):
    """Orders, payments, refunds, tax documents and stock.

    Models and entry points: ``docs/app-map.md``. Read
    ``docs/order/index.md`` before changing anything here: it names the file
    of the order reference for each subject.
    """

    name = "order"
    verbose_name = _("Orders")

    def ready(self):
        import order.signals.handlers  # noqa
