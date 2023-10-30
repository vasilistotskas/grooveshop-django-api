import json

from django.contrib.auth.signals import user_logged_in
from django.contrib.auth.signals import user_logged_out
from django.core.serializers.json import DjangoJSONEncoder
from django.dispatch import receiver
from django.utils.timezone import now
from rest_framework.request import Request

from cart.service import CartService
from cart.service import ProcessCartOption
from core import caches
from core.caches import cache_instance
from core.caches import generate_user_cache_key
from user.models import UserAccount


@receiver(user_logged_in)
def update_session_user_log_in(sender, request: Request, **kwargs):
    try:
        try:
            pre_log_in_cart_id = request.session["pre_log_in_cart_id"]
        except KeyError:
            pre_log_in_cart_id = None

        cart_service = CartService(request=request)
        cart_service.process_cart(request, option=ProcessCartOption.MERGE)
        cart_id = cart_service.cart.id

        json_user = json.dumps(
            {"id": request.user.id, "email": request.user.email},
            cls=DjangoJSONEncoder,
        )

        last_activity = None
        if hasattr(request, "session") and hasattr(request.session, "last_activity"):
            last_activity = request.session["last_activity"]

        user_cache_key = generate_user_cache_key(request)
        cache_instance.set(
            user_cache_key,
            {
                "last_activity": last_activity,
                "user": json_user,
                "cart_id": cart_id,
                "pre_log_in_cart_id": pre_log_in_cart_id,
                "referer": request.META.get("HTTP_REFERER", None),
                "session_key": request.session.session_key,
            },
            caches.ONE_HOUR,
        )

        # update last login for session
        now_str = now().isoformat()
        request.session["last_login"] = now_str
        request.session.save()

    except AttributeError:
        pass


@receiver(user_logged_out)
def update_session_user_log_out(sender, request: Request, user, **kwargs):
    try:
        cart_service = CartService(request=request)

        UserAccount.remove_session(request.user, request)

        cache_instance.delete(
            f"{caches.USER_AUTHENTICATED}{user.id}:" f"{request.session.session_key}"
        )
        cart_service.process_cart(request, option=ProcessCartOption.CLEAN)

    except AttributeError:
        pass
