from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TagConfig(AppConfig):
    name = "tag"
    verbose_name = _("Tags")
