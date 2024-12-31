from __future__ import annotations

from datetime import timedelta
from os import getenv

from allauth.usersessions.models import UserSession
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response

from core.caches import cache_instance
from core.logging import LogInfo
from session.serializers import ActiveUsersCountSerializer

User = get_user_model()


@extend_schema(
    description=_("Returns the number of active users in the last hour."),
    request=None,
    responses=ActiveUsersCountSerializer,
    methods=["GET"],
)
@api_view(["GET"])
@permission_classes([IsAdminUser])
def active_users_count(request):
    active_user_ids = set()
    prefix = getenv("DEFAULT_CACHE_KEY_PREFIX")
    version = getenv("DEFAULT_CACHE_VERSION")
    filter_prefix = f"{prefix}:{version}:django.contrib.sessions.cache"
    one_hour_ago = timezone.now() - timedelta(hours=1)

    try:
        session_keys = cache_instance.keys("django.contrib.sessions.cache*")
    except Exception as exc:
        LogInfo.error("Error retrieving cache keys: %s", str(exc))
        return Response({"active_users": 0}, status=status.HTTP_200_OK)

    for key in session_keys:
        if key.startswith(filter_prefix):
            session_key = key[len(filter_prefix) :]
        else:
            continue

        try:
            user_data = cache_instance.get(key)
            if not user_data:
                continue
        except Exception as exc:
            LogInfo.error(
                "Error retrieving cache data for key %s: %s", key, str(exc)
            )
            continue

        session_exists = UserSession.objects.filter(
            session_key=session_key, last_seen_at__gte=one_hour_ago
        ).exists()

        if session_exists:
            try:
                session = UserSession.objects.get(session_key=session_key)
                if session.user_id:
                    active_user_ids.add(session.user_id)
            except UserSession.DoesNotExist:
                continue

    serializer = ActiveUsersCountSerializer(
        {"active_users": len(active_user_ids)}
    )
    return Response(serializer.data, status=status.HTTP_200_OK)
