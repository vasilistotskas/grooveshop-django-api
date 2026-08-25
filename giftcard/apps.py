from django.apps import AppConfig


class GiftcardConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "giftcard"

    def ready(self):
        import giftcard.signals  # noqa: F401
