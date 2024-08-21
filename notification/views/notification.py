from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from notification.models.notification import Notification
from notification.serializers.notification import NotificationSerializer


@extend_schema(
    description=_("Returns the notifications for a list of ids."),
    request=None,
    responses=NotificationSerializer(many=True),
    methods=["POST"],
)
@api_view(["POST"])
def notifications_by_ids(request):
    notification_ids = request.data.get("ids", [])
    if not notification_ids:
        return Response(
            {"error": _("No notification ids provided.")},
            status=status.HTTP_400_BAD_REQUEST,
        )

    notifications = Notification.objects.filter(id__in=notification_ids)
    if not notifications:
        return Response(
            {"error": _("No notifications found for the provided ids.")},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = NotificationSerializer(notifications, many=True)
    return Response(serializer.data)
