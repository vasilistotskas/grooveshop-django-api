import json
from unittest.mock import Mock
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.serializers.json import DjangoJSONEncoder
from django.test import RequestFactory
from django.test import TestCase
from django.utils import timezone

from cart.models import Cart
from cart.service import CartService
from core import caches
from core.caches import cache_instance
from session.middleware import SessionTraceMiddleware

User = get_user_model()


class SessionTraceMiddlewareTest(TestCase):
    middleware: SessionTraceMiddleware = None
    factory: RequestFactory = None

    def setUp(self):
        self.middleware = SessionTraceMiddleware(Mock())
        self.factory = RequestFactory()

    def test_process_user_data_authenticated(self):
        request = self.factory.get("/")

        # Manually apply session middleware to the request
        session_middleware = SessionMiddleware(self.middleware)
        session_middleware.process_request(request)

        user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        request.user = user
        self.middleware.process_user_data(request)
        self.assertEqual(
            request.session["user"],
            json.dumps({"id": user.id, "email": user.email}, cls=DjangoJSONEncoder),
        )

    def test_process_user_data_unauthenticated(self):
        request = self.factory.get("/")

        # Manually apply session middleware to the request
        session_middleware = SessionMiddleware(self.middleware)
        session_middleware.process_request(request)

        self.middleware.process_user_data(request)
        self.assertIsNone(request.session.get("user"))

    def test_ensure_cart_id_existing_cart_id(self):
        request = self.factory.get("/")

        # Manually apply session middleware to the request
        session_middleware = SessionMiddleware(self.middleware)
        session_middleware.process_request(request)

        request.session["cart_id"] = 123
        self.middleware.ensure_cart_id(request)
        self.assertEqual(request.session["cart_id"], 123)

    @patch.object(CartService, "get_or_create_cart")
    def test_ensure_cart_id_new_cart(self, mock_get_or_create_cart):
        mock_get_or_create_cart.return_value = Cart(id=456)
        request = self.factory.get("/")

        # Manually set the user attribute on the request
        request.user = AnonymousUser()

        # Manually apply session middleware to the request
        session_middleware = SessionMiddleware(self.middleware)
        session_middleware.process_request(request)

        self.middleware.ensure_cart_id(request)
        self.assertEqual(request.session["pre_log_in_cart_id"], 456)
        self.assertEqual(request.session["cart_id"], 456)

    def test_update_cache_authenticated(self):
        user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        request = self.factory.get("/")

        # Manually apply session middleware to the request
        session_middleware = SessionMiddleware(self.middleware)
        session_middleware.process_request(request)

        request.user = user
        request.session["last_activity"] = timezone.now()
        request.session["user"] = json.dumps({"id": user.id, "email": user.email})
        request.META["HTTP_REFERER"] = "http://example.com"
        request.session["cart_id"] = 789
        with patch.object(cache_instance, "set") as mock_set:
            self.middleware.update_cache(request)
            expected_data = {
                "last_activity": request.session["last_activity"],
                "user": request.session["user"],
                "referer": request.META.get("HTTP_REFERER"),
                "session_key": request.session.session_key,
                "cart_id": request.session["cart_id"],
            }
            mock_set.assert_called_once_with(
                f"{caches.USER_AUTHENTICATED}_{user.id}", expected_data, caches.ONE_HOUR
            )

    def test_update_cache_unauthenticated(self):
        request = self.factory.get("/")

        # Manually apply session middleware to the request
        session_middleware = SessionMiddleware(self.middleware)
        session_middleware.process_request(request)

        request.user = AnonymousUser()
        request.session["last_activity"] = timezone.now()
        request.session["user"] = None
        request.META["HTTP_REFERER"] = "http://example.com"

        # Manually set the session_key attribute if it's None
        if request.session.session_key is None:
            request.session.create()

        request.session.modified = True  # Mark the session as modified

        request.session["cart_id"] = 789
        with patch.object(cache_instance, "set") as mock_set:
            self.middleware.update_cache(request)
            expected_data = {
                "last_activity": request.session["last_activity"],
                "user": request.session["user"],
                "referer": request.META.get("HTTP_REFERER"),
                "session_key": request.session.session_key,
                "cart_id": request.session.get(
                    "cart_id"
                ),  # Use .get() to handle None case
            }
            mock_set.assert_called_once_with(
                f"{caches.USER_UNAUTHENTICATED}_{request.session.session_key}",
                expected_data,
                caches.ONE_HOUR,
            )

    def tearDown(self) -> None:
        super().tearDown()
        pass
