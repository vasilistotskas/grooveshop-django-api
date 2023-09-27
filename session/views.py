from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
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
from session.serializers import ClearAllUserSessionsSerializer
from session.serializers import RefreshLastActivitySerializer
from session.serializers import SessionSerializer
from user.models import UserAccount

User = get_user_model()


@extend_schema(
    description=_("Returns whether the user is authenticated or not."),
    request=None,
    responses=SessionSerializer,
    methods=["GET"],
)
@api_view(["GET"])
def session_view(request):
    if not request.user.is_authenticated:
        data = {"isSessionAuthenticated": False}
    else:
        data = {"isSessionAuthenticated": True}

    serializer = SessionSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    description=_("Clears all user sessions."),
    request=None,
    responses=ClearAllUserSessionsSerializer,
    methods=["POST"],
)
@api_view(["POST"])
def clear_all_user_sessions(request):
    if not request.user.is_authenticated:
        return Response("Forbidden", status=status.HTTP_403_FORBIDDEN)

    user = get_object_or_404(UserAccount, email=request.user)

    UserAccount.remove_all_sessions(user, request)
    serializer = ClearAllUserSessionsSerializer(data={"success": True})
    serializer.is_valid(raise_exception=True)
    return Response(serializer.data, status=status.HTTP_200_OK)


@extend_schema(
    description=_("Refreshes the last activity of the user."),
    request=None,
    responses=RefreshLastActivitySerializer,
    methods=["POST"],
)
@api_view(["POST"])
def refresh_last_activity(request):
    if not request.user.is_authenticated:
        serializer = RefreshLastActivitySerializer(data={"success": False})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data, status=status.HTTP_403_FORBIDDEN)

    try:
        session = request.session
        session["last_activity"] = timezone.now()
        session.save()
        serializer = RefreshLastActivitySerializer(data={"success": True})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
    except AttributeError:
        serializer = RefreshLastActivitySerializer(data={"success": False})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.data, status=status.HTTP_400_BAD_REQUEST)


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
