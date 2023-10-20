import logging
import os

from django.utils import timezone

from core import caches
from core.caches import cache_instance
from core.caches import generate_user_cache_key

logger = logging.getLogger(__name__)


class SessionTraceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if os.getenv("SYSTEM_ENV", "development") == "GITHUB_WORKFLOW":
            return self.get_response(request)

        response = self.get_response(request)

        self.update_session(request)

        return response

    def update_session(self, request):
        user_cache_key = generate_user_cache_key(request)

        now = timezone.now()
        user = request.session.get("user", None)
        http_referer = request.META.get("HTTP_REFERER", None)
        user_agent = request.META.get("HTTP_USER_AGENT", None)
        session_key = request.session.session_key
        cart_id = request.session.get("cart_id", None)

        request.session["last_activity"] = now
        request.session["user"] = user
        request.META["HTTP_REFERER"] = http_referer
        request.META["HTTP_USER_AGENT"] = user_agent
        request.session["session_key"] = session_key
        request.session["cart_id"] = cart_id
        request.session.modified = True
        request.session.save()

        cache_data = {
            "last_activity": now,
            "user": user,
            "referer": http_referer,
            "user_agent": user_agent,
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
