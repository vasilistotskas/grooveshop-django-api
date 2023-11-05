import json
import logging
import os

from django.core.serializers.json import DjangoJSONEncoder
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
        try:
            user_cache_key = generate_user_cache_key(request)

            now = timezone.now()
            now_str = now.isoformat()
            user = request.session.get("user", None)
            http_referer = request.META.get("HTTP_REFERER", None)
            user_agent = request.META.get("HTTP_USER_AGENT", None)
            session_key = request.session.session_key
            cart_id = request.session.get("cart_id", None)

            request.session["last_activity"] = now_str
            request.META["HTTP_REFERER"] = http_referer
            request.META["HTTP_USER_AGENT"] = user_agent
            request.session["session_key"] = session_key
            request.session["cart_id"] = cart_id
            request.session.modified = True
            request.session.save()

            if user is not None:
                json_user = json.dumps(
                    {"id": request.user.id, "email": request.user.email},
                    cls=DjangoJSONEncoder,
                )
            else:
                json_user = None

            cache_data = {
                "last_activity": now_str,
                "user": json_user,
                "referer": http_referer,
                "user_agent": user_agent,
                "session_key": session_key,
                "cart_id": cart_id,
            }
            cache_instance.set(user_cache_key, cache_data, caches.ONE_HOUR)
        except Exception as e:
            logger.error(
                "SessionTraceMiddleware failed to update session",
                extra={
                    "request": request,
                    "exception": e,
                },
            )

    def log_request(self, request, response):
        logger.info(
            "SessionTraceMiddleware processed request",
            extra={
                "request": request,
                "response": response,
            },
        )
