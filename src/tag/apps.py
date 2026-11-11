from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TagConfig(AppConfig):
    """Tags and the generic relation that attaches them.

    Models and entry points: ``docs/app-map.md``.
    """

    name = "tag"
    verbose_name = _("Tags")
