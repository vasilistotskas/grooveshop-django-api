from django.contrib.auth.signals import user_logged_in
from django.contrib.auth.signals import user_logged_out
from django.core.serializers import serialize
from django.dispatch import receiver
from django.utils.timezone import now

from cart.service import CartService
from core import caches


@receiver(user_logged_in)
def update_session_user_log_in(sender, request, user, **kwargs):
    try:
        request.session["user"] = user

        try:
            pre_log_in_cart_id = request.session["pre_log_in_cart_id"]
        except KeyError:
            pre_log_in_cart_id = None

        caches.set(
            str(user.id),
            pre_log_in_cart_id,
            caches.ONE_HOUR,
        )

        cart_service = CartService(request)
        cart_service.process_user_cart(request, option="merge")
        cart_id = cart_service.cart.id

        json_user = serialize(
            "json",
            [
                user,
            ],
        )

        last_activity = None
        if hasattr(request, "session") and hasattr(request.session, "last_activity"):
            last_activity = request.session["last_activity"]

        caches.set(
            caches.USER + "_" + str(user.id),
            {
                "last_activity": last_activity,
                "user": json_user,
                "cart_id": cart_id,
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
        caches.delete(caches.USER + "_" + str(user.id))
    except AttributeError:
        pass
