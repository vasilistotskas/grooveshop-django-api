from django.db import models
from django.utils.translation import gettext_lazy as _


class TipKindEnum(models.TextChoices):
    SUCCESS = "SUCCESS", _("Success")
    INFO = "INFO", _("Info")
    ERROR = "ERROR", _("Error")
    WARNING = "WARNING", _("Warning")
