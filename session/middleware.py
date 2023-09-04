import json
import logging
import os

from django.core.exceptions import SuspiciousOperation
from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError
from django.utils import timezone

from cart.service import CartService
from core import caches
from core.caches import cache_instance

logger = logging.getLogger(__name__)


class SessionTraceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if os.environ.get("SYSTEM_ENV") == "GITHUB_WORKFLOW":
            return self.get_response(request)

        response = self.get_response(request)

        self.process_user_data(request)
        self.ensure_cart_id(request)
        # self.save_session(request, response)
        self.update_cache(request)

        return response

    def process_user_data(self, request):
        if not hasattr(request, "user"):
            request.session["user"] = None
            return

        if request.user.is_authenticated:
            json_user = json.dumps(
                {"id": request.user.id, "email": request.user.email},
                cls=DjangoJSONEncoder,
            )
            request.session["user"] = json_user
            return

        request.session["user"] = None

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
            self.save_session(request, None)

    def save_session(self, request, response):
        request.session["last_activity"] = timezone.now()
        request.session["referer"] = request.META.get("HTTP_REFERER", None)

        try:
            request.session.save()
        except (DatabaseError, SuspiciousOperation) as e:
            logger.error(
                "SessionTraceMiddleware error",
                extra={
                    "request": request,
                    "response": response,
                    "exception": e,
                },
            )
            raise e

    def update_cache(self, request):
        # Make a cache key for the user, if the there is no user, use the session key
        user_cache_key = (
            str(caches.USER_AUTHENTICATED) + "_" + str(request.user.id)
            if request.user.is_authenticated
            else str(caches.USER_UNAUTHENTICATED) + "_" + request.session.session_key
        )
        cache_data = {
            "last_activity": request.session["last_activity"],
            "user": request.session["user"],
            "referer": request.META.get("HTTP_REFERER", None),
            "session_key": request.session.session_key,
            "cart_id": request.session["cart_id"]
            if "cart_id" in request.session
            else None,
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
