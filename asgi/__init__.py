import os

from channels.routing import ProtocolTypeRouter
from channels.routing import URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application
from django.urls import path

from asgi.cors_handler import cors_handler
from asgi.gzip_compression import gzip_compression
from asgi.health_check import health_check
from core.middleware.channels import TokenAuthMiddlewareStack
from notification.consumers import NotificationConsumer

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")

django_asgi_app = get_asgi_application()
django_asgi_app = health_check(
    django_asgi_app, "/health/"
)  # type: ignore[arg-type] # Django's ASGI app is less strict than the spec # noqa: E501
django_asgi_app = gzip_compression(django_asgi_app)
django_asgi_app = cors_handler(django_asgi_app)

websocket_urlpatterns = [path("ws/notifications", NotificationConsumer.as_asgi())]


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(TokenAuthMiddlewareStack(URLRouter(websocket_urlpatterns))),
    }
)
