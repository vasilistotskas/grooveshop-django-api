import json
from unittest.mock import Mock
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.test import TestCase
from django.utils import timezone

from cart.models import Cart
from cart.service import CartService
from core.caches import cache_instance
from core.caches import generate_user_cache_key
from session.middleware import SessionTraceMiddleware

User = get_user_model()


class SessionTraceMiddlewareTest(TestCase):
    middleware: SessionTraceMiddleware = None
    factory: RequestFactory = None

    def setUp(self):
        self.middleware = SessionTraceMiddleware(Mock())
        self.factory = RequestFactory()

    def test_ensure_cart_id_existing_cart_id(self):
        request = self.factory.get("/")

        session_middleware = SessionMiddleware(self.middleware)
        session_middleware.process_request(request)

        request.session["cart_id"] = 123
        self.middleware.ensure_cart_id(request)
        self.assertEqual(request.session["cart_id"], 123)

    @patch.object(CartService, "get_or_create_cart")
    def test_ensure_cart_id_new_cart(self, mock_get_or_create_cart):
        mock_get_or_create_cart.return_value = Cart(id=456)
        request = self.factory.get("/")

        request.user = AnonymousUser()

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
        request.user = user

        session_middleware = SessionMiddleware(self.middleware)
        session_middleware.process_request(request)
        request.session.create()

        user_cache_key = generate_user_cache_key(request)
        self.middleware.update_session(request)

        expected_cache = {
            "session_key": request.session.session_key,
            "last_activity": request.session["last_activity"],
            "user": request.session["user"],
            "referer": request.META["HTTP_REFERER"],
            "cart_id": request.session["cart_id"],
        }
        cache = cache_instance.get(user_cache_key)

        self.assertEqual(cache, expected_cache)

    def test_update_cache_unauthenticated(self):
        request = self.factory.get("/")

        session_middleware = SessionMiddleware(self.middleware)
        session_middleware.process_request(request)
        request.session.create()

        request.user = AnonymousUser()
        request.session.modified = True

        non_user_cache_key = generate_user_cache_key(request)
        self.middleware.update_session(request)

        expected_cache = {
            "session_key": request.session.session_key,
            "last_activity": request.session["last_activity"],
            "user": request.session["user"],
            "referer": request.META["HTTP_REFERER"],
            "cart_id": request.session["cart_id"],
        }
        cache = cache_instance.get(non_user_cache_key)

        self.assertEqual(
            cache,
            expected_cache,
        )

    def tearDown(self) -> None:
        super().tearDown()
        pass
