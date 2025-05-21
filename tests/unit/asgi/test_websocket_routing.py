from channels.routing import ProtocolTypeRouter

import asgi
from notification.consumers import NotificationConsumer


class TestAsgiWebsocketRouting:
    def test_asgi_application_structure(self):
        assert isinstance(asgi.application, ProtocolTypeRouter)

        assert "http" in asgi.application.application_mapping
        assert "websocket" in asgi.application.application_mapping

    def test_websocket_urlpatterns(self):
        assert hasattr(asgi, "websocket_urlpatterns")
        assert isinstance(asgi.websocket_urlpatterns, list)

        path_strings = [str(p.pattern) for p in asgi.websocket_urlpatterns]
        assert "ws/notifications/" in "".join(path_strings)

    def test_notification_consumer_route(self):
        notification_route = None
        for route in asgi.websocket_urlpatterns:
            if str(route.pattern) == "ws/notifications/":
                notification_route = route
                break

        assert notification_route is not None

        assert (
            notification_route.callback.consumer_class == NotificationConsumer
        )

    def test_websocket_middleware_configuration(self):
        websocket_app = asgi.application.application_mapping["websocket"]

        assert "Validator" in websocket_app.__class__.__name__

        assert websocket_app is not None

        assert hasattr(asgi, "websocket_urlpatterns")
        assert len(asgi.websocket_urlpatterns) > 0
        assert any(
            "notifications" in str(p.pattern)
            for p in asgi.websocket_urlpatterns
        )
