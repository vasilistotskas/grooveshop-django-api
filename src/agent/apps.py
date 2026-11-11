from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class AgentConfig(AppConfig):
    """The OAuth-scoped ``/api/v1/agent/*`` surface the agent gateway calls:
    shopper tools and read-only store-admin tools. No models.

    Models and entry points: ``docs/app-map.md``.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "agent"
    verbose_name = _("Agent API")
