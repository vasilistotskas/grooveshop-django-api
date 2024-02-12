from django.db import models
from django.utils.translation import gettext_lazy as _


class SettingsValueTypeEnum(models.TextChoices):
    STRING = "str", _("String")
    INTEGER = "int", _("Integer")
    BOOLEAN = "bool", _("Boolean")
    DICTIONARY = "dict", _("Dictionary")
    LIST = "list", _("List")
    FLOAT = "float", _("Float")
