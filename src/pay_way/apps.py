from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PayWayConfig(AppConfig):
    """A store's payment methods and the shipping methods each one excludes.

    Models and entry points: ``docs/app-map.md``. Read
    ``docs/order/payments.md``.
    """

    name = "pay_way"
    verbose_name = _("Payment methods")
