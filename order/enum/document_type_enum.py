from django.db import models
from django.utils.translation import gettext_lazy as _


class OrderDocumentTypeEnum(models.TextChoices):
    RECEIPT = "RECEIPT", _("Receipt")
    INVOICE = "INVOICE", _("Invoice")
