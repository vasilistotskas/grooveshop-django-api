from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core import caches
from core.caches import cache_instance
from session.serializers import ActiveUsersCountSerializer

User = get_user_model()


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
