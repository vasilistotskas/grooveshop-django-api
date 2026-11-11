from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ProductConfig(AppConfig):
    """The catalogue: products, categories, brands, attributes, reviews,
    favourites, alerts and pricing.

    Models and entry points: ``docs/app-map.md``. Read ``docs/product-
    pricing.md``.
    """

    name = "product"
    verbose_name = _("Products")

    def ready(self):
        from . import signals  # noqa
