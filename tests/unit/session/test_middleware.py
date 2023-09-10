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
        user = User.objects.create_user(
            email="testuser@example.com", password="testpassword"
        )
        request = self.factory.get("/")
        request.user = user

        session_middleware = SessionMiddleware(self.middleware)
        session_middleware.process_request(request)

        self.middleware.process_user_data(request)
        self.assertEqual(
            request.session["user"],
            json.dumps({"id": user.id, "email": user.email}, cls=DjangoJSONEncoder),
        )

    def test_process_user_data_unauthenticated(self):
        request = self.factory.get("/")

        session_middleware = SessionMiddleware(self.middleware)
        session_middleware.process_request(request)

        self.middleware.process_user_data(request)
        self.assertIsNone(request.session.get("user"))

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

        request.session["last_activity"] = timezone.now()
        request.session["user"] = json.dumps({"id": user.id, "email": user.email})
        request.META["HTTP_REFERER"] = "http://example.com"
        request.session["cart_id"] = 789

        user_cache_key = caches.USER_AUTHENTICATED + "_" + str(user.id)
        self.middleware.update_cache(request)

        cache = cache_instance.get(user_cache_key)
        expected_cache = {
            "session_key": request.session.session_key,
            "last_activity": request.session["last_activity"],
            "user": request.session["user"],
            "referer": request.META["HTTP_REFERER"],
            "cart_id": request.session["cart_id"],
        }

        self.assertEqual(cache, expected_cache)

    def test_update_cache_unauthenticated(self):
        request = self.factory.get("/")

        session_middleware = SessionMiddleware(self.middleware)
        session_middleware.process_request(request)
        request.session.create()

        request.user = AnonymousUser()
        request.session["last_activity"] = timezone.now()
        request.session["user"] = None
        request.META["HTTP_REFERER"] = "http://example.com"
        request.session["cart_id"] = 789
        request.session.modified = True

        non_user_cache_key = (
            str(caches.USER_UNAUTHENTICATED) + "_" + request.session.session_key
        )
        self.middleware.update_cache(request)

        cache = cache_instance.get(non_user_cache_key)
        expected_cache = {
            "session_key": request.session.session_key,
            "last_activity": request.session["last_activity"],
            "user": request.session["user"],
            "referer": request.META["HTTP_REFERER"],
            "cart_id": request.session["cart_id"],
        }

        self.assertEqual(
            cache,
            expected_cache,
        )

    def tearDown(self) -> None:
        super().tearDown()
        pass
