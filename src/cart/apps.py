from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CartConfig(AppConfig):
    """Carts, their lines and first-touch attribution, and the agent's cart
    token.

    Models and entry points: ``docs/app-map.md``. Read ``docs/cart-agent-
    token.md`` for the token.
    """

    name = "cart"
    verbose_name = _("Carts")
