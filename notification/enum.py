from django.db import models
from django.utils.translation import gettext_lazy as _


class NotificationKindEnum(models.TextChoices):
    ERROR = "ERROR", _("Error")
    SUCCESS = "SUCCESS", _("Success")
    INFO = "INFO", _("Info")
    WARNING = "WARNING", _("Warning")
    DANGER = "DANGER", _("Danger")
