from __future__ import annotations
from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import (
    extend_schema,
    inline_serializer,
    OpenApiParameter,
)
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from notification.models.notification import Notification
from notification.models.user import NotificationUser
from notification.serializers.notification import NotificationSerializer


@extend_schema(
    operation_id="getNotificationsByIds",
    summary=_("Returns the notifications for a list of ids."),
    description=_("Returns the notifications for a list of ids."),
    tags=["Notifications"],
    parameters=[
        OpenApiParameter(
            name="seen",
            type=bool,
            location=OpenApiParameter.QUERY,
            required=False,
            description=_(
                "Filter notifications by seen status. If false, returns only unseen notifications."
            ),
        ),
    ],
    request=inline_serializer(
        name="NotificationIdsSerializer",
        fields={"ids": serializers.ListField(child=serializers.IntegerField())},
    ),
    responses=NotificationSerializer(many=True),
    methods=["POST"],
)
@api_view(["POST"])
def notifications_by_ids(request):
    seen_query = request.query_params.get("seen", None)
    notification_ids = request.data.get("ids", [])

    if not notification_ids:
        return Response(
            {"error": _("No notification ids provided.")},
            status=status.HTTP_400_BAD_REQUEST,
        )

    notifications = Notification.objects.filter(id__in=notification_ids)

    if seen_query is not None:
        user = request.user
        seen_value = seen_query.lower() == "true"

        if seen_value:
            notifications = notifications.filter(
                user__user=user, user__seen=True
            )
        else:
            seen_notification_ids = NotificationUser.objects.filter(
                user=user, notification_id__in=notification_ids, seen=True
            ).values_list("notification_id", flat=True)

            notifications = notifications.exclude(id__in=seen_notification_ids)

    if not notifications.exists():
        return Response(
            {"error": _("No notifications found for the provided ids.")},
            status=status.HTTP_404_NOT_FOUND,
        )

    serializer = NotificationSerializer(notifications, many=True)
    return Response(serializer.data)
