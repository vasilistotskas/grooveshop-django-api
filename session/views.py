from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.middleware.csrf import get_token
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from core import caches
from core.caches import cache_instance
from session.serializers import ActiveUsersCountSerializer
from session.serializers import RevokeAllUserSessionsSerializer
from session.serializers import RevokeSessionSerializer
from session.serializers import SessionSerializer
from user.enum.account import UserRole
from user.models import UserAccount

User = get_user_model()


@extend_schema(
    description=_("Returns the session data."),
    request=None,
    responses=SessionSerializer,
    methods=["GET"],
)
@api_view(["GET"])
def session_view(request):
    if not request.session.session_key:
        request.session.create()

    data = {
        "is_session_authenticated": True if request.user.is_authenticated else False,
        "CSRF_token": get_token(request),
        "referer": request.META.get("HTTP_REFERER", None),
        "user_agent": request.META.get("HTTP_USER_AGENT", None),
        "sessionid": request.session.session_key,
        "role": request.user.role.value
        if request.user.is_authenticated
        else UserRole.GUEST.value,
        "last_activity": request.session.get("last_activity", None),
    }

    serializer = SessionSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    description=_("Clears all user sessions."),
    request=None,
    responses=RevokeAllUserSessionsSerializer,
    methods=["DELETE"],
)
@api_view(["DELETE"])
def revoke_all_user_sessions(request):
    if not request.user.is_authenticated:
        return Response("Forbidden", status=status.HTTP_403_FORBIDDEN)

    user = get_object_or_404(UserAccount, email=request.user)

    UserAccount.remove_all_sessions(user, request)
    serializer = RevokeAllUserSessionsSerializer(data={"success": True})
    serializer.is_valid(raise_exception=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    description=_("Clears a user session."),
    request=None,
    responses=RevokeSessionSerializer,
    methods=["DELETE"],
)
@api_view(["DELETE"])
def revoke_session(request):
    UserAccount.remove_session(request.user, request)
    serializer = RevokeSessionSerializer(data={"success": True})
    serializer.is_valid(raise_exception=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    description=_("Returns the number of active users."),
    request=None,
    responses=ActiveUsersCountSerializer,
    methods=["GET"],
)
@api_view(["GET"])
def active_users_count(request):
    active_users = 0
    last_activity_threshold = timezone.now() - timedelta(minutes=15)

    # Iterate through the cached user data and count active users
    for key in cache_instance.keys(f"{caches.USER_AUTHENTICATED}*"):
        user_data = cache_instance.get(key)
        if not user_data:
            continue
        last_activity = user_data.get("last_activity")
        if last_activity and last_activity > last_activity_threshold:
            active_users += 1

    return Response({"active_users": active_users})
