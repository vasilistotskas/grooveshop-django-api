from django.db import models
from django.utils.translation import gettext_lazy as _


class NotificationKindEnum(models.TextChoices):
    ERROR = "error", _("Error")
    SUCCESS = "success", _("Success")
    INFO = "info", _("Info")
    WARNING = "warning", _("Warning")
    DANGER = "danger", _("Danger")
