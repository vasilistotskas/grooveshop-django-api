import json

from django.contrib.auth.signals import user_logged_in
from django.contrib.auth.signals import user_logged_out
from django.core.serializers.json import DjangoJSONEncoder
from django.dispatch import receiver
from django.utils.timezone import now

from cart.service import CartService
from cart.service import ProcessUserCartOption
from core import caches
from core.caches import cache_instance


@receiver(user_logged_in)
def update_session_user_log_in(sender, request, user, **kwargs):
    try:
        request.session["user"] = user

        try:
            pre_log_in_cart_id = request.session["pre_log_in_cart_id"]
        except KeyError:
            pre_log_in_cart_id = None

        cart_service = CartService(request)
        cart_service.process_user_cart(request, option=ProcessUserCartOption.MERGE)
        cart_id = cart_service.cart.id

        json_user = json.dumps(
            {"id": request.user.id, "email": request.user.email},
            cls=DjangoJSONEncoder,
        )

        last_activity = None
        if hasattr(request, "session") and hasattr(request.session, "last_activity"):
            last_activity = request.session["last_activity"]

        cache_instance.set(
            caches.USER_AUTHENTICATED + "_" + str(user.id),
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
        request.session["last_login"] = now()
        request.session.save()

    except AttributeError:
        pass


@receiver(user_logged_out)
def update_session_user_log_out(sender, request, user, **kwargs):
    try:
        request.session["user"] = None
        request.session.save()
        cache_instance.delete(caches.USER_AUTHENTICATED + "_" + str(user.id))
    except AttributeError:
        pass
