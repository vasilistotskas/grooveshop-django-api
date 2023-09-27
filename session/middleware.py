import logging
import os

from django.utils import timezone

from cart.service import CartService
from core import caches
from core.caches import cache_instance
from core.caches import generate_user_cache_key

logger = logging.getLogger(__name__)


class SessionTraceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if os.environ.get("SYSTEM_ENV") == "GITHUB_WORKFLOW":
            return self.get_response(request)

        response = self.get_response(request)

        self.ensure_cart_id(request)
        self.update_session(request)

        return response

    def ensure_cart_id(self, request):
        # if request session has not attribute cart_id
        if not hasattr(request.session, "cart_id"):
            cart_service = CartService(request)
            if cart_service.cart is None:
                cart_service.cart = cart_service.get_or_create_cart()
            pre_log_in_cart_id = cart_service.cart.id
            cart_id = cart_service.cart.id
            request.session["pre_log_in_cart_id"] = pre_log_in_cart_id
            request.session["cart_id"] = cart_id
            return

    def update_session(self, request):
        user_cache_key = generate_user_cache_key(request)
        now = timezone.now()
        user = request.session.get("user", None)
        http_referer = request.META.get("HTTP_REFERER", None)
        session_key = request.session.session_key
        cart_id = request.session.get("cart_id", None)

        request.session["last_activity"] = now
        request.session["user"] = user
        request.META["HTTP_REFERER"] = http_referer
        request.session["session_key"] = session_key
        request.session["cart_id"] = cart_id

        cache_data = {
            "last_activity": now,
            "user": user,
            "referer": http_referer,
            "session_key": session_key,
            "cart_id": cart_id,
        }
        cache_instance.set(user_cache_key, cache_data, caches.ONE_HOUR)

    def log_request(self, request, response):
        logger.info(
            "SessionTraceMiddleware processed request",
            extra={
                "request": request,
                "response": response,
            },
        )
