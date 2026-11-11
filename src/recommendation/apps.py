from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class RecommendationConfig(AppConfig):
    """Product recommendations: slots, candidates, events and the strategy
    engine.

    Models and entry points: ``docs/app-map.md``. Read
    ``docs/recommendations-engine.md``.
    """

    name = "recommendation"
    verbose_name = _("Recommendations")

    def ready(self):
        # Importing the package runs every @register_strategy. Nothing
        # else here reads the registry at startup, so an import-time
        # failure in a strategy module surfaces as a boot failure — the
        # right place to find out, rather than an empty strip in prod.
        import recommendation.strategies  # noqa: F401
        from recommendation.signals import connect_signals

        connect_signals()
