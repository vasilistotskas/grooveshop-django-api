import os

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application
from django.urls import path

from asgi.cors_handler import cors_handler
from asgi.health_check import health_check

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")

django_asgi_app = get_asgi_application()
django_asgi_app = health_check(
    django_asgi_app, "/health/"
)  # Django's ASGI app is less strict than the spec

from core.middleware.channels import TokenAuthMiddlewareStack  # noqa: E402
from notification.consumers import NotificationConsumer  # noqa: E402
from tenant.middleware_ws import TenantWebsocketMiddleware  # noqa: E402

websocket_urlpatterns = [
    path("ws/notifications/", NotificationConsumer.as_asgi())
]

application = ProtocolTypeRouter(
    {
        "http": cors_handler(django_asgi_app),
        "websocket": AllowedHostsOriginValidator(
            TenantWebsocketMiddleware(
                TokenAuthMiddlewareStack(URLRouter(websocket_urlpatterns))
            )
        ),
    }
)

__all__ = ["application", "cors_handler"]
