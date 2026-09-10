from django.apps import AppConfig


class RecommendationConfig(AppConfig):
    name = "recommendation"

    def ready(self):
        # Importing the package runs every @register_strategy. Nothing
        # else here reads the registry at startup, so an import-time
        # failure in a strategy module surfaces as a boot failure — the
        # right place to find out, rather than an empty strip in prod.
        import recommendation.strategies  # noqa: F401
        from recommendation.signals import connect_signals

        connect_signals()
