from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.utils import timezone
from django.utils.timezone import now
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.generics import GenericAPIView
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from core import caches
from core.caches import cache_instance
from user.models import UserAccount
from user.serializers.account import UserAccountSerializer

User = get_user_model()


@ensure_csrf_cookie
def session_view(request):
    if not request.user.is_authenticated:
        return JsonResponse({"isAuthenticated": False})

    return JsonResponse({"isAuthenticated": True})


class ClearAllUserSessions(GenericAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = UserAccountSerializer

    def post(self, request, format=None):
        if not request.user.is_authenticated:
            return Response("Forbidden", status=status.HTTP_403_FORBIDDEN)

        user = get_object_or_404(UserAccount, email=self.request.user)
        UserAccount.remove_all_sessions(user, request)

        return Response("Success", status=status.HTTP_200_OK)


class ActiveUserViewSet(ViewSet):
    serializer_class = UserAccountSerializer

    @action(detail=False, methods=["post"])
    def refresh_last_activity(self, request):
        try:
            session = request.session
            session.last_activity = now()
            session.save()
            return Response({"success": True})
        except AttributeError:
            return Response({"success": False}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=["get"])
    def active_users_count(self, request):
        active_users = 0
        last_activity_threshold = timezone.now() - timedelta(minutes=15)

        # Iterate through the cached user data and count active users
        for key in cache_instance.keys(caches.USER_AUTHENTICATED + "_*"):
            user_data = cache_instance.get(key)
            if not user_data:
                continue
            last_activity = user_data.get("last_activity")
            if last_activity and last_activity > last_activity_threshold:
                active_users += 1

        return Response({"active_users": active_users})
