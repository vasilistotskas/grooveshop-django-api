from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PayWayConfig(AppConfig):
    name = "pay_way"
    verbose_name = _("Payment methods")
