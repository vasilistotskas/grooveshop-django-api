from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.serializers import ErrorResponseSerializer
from user.serializers.email_preferences import (
    EmailPreferencesSerializer,
    EmailPreferencesWriteSerializer,
)
from user.services.subscription import (
    MarketingPreference,
    marketing_preferences,
    set_marketing_preferences,
)


def _body(preferences: list[MarketingPreference]) -> dict:
    return {
        "types": [
            {
                "key": preference.email_type.key,
                "title": preference.email_type.title,
                "enabled": preference.enabled,
            }
            for preference in preferences
        ]
    }


class EmailPreferencesView(APIView):
    """The signed-in customer's marketing email choices.

    Only the account's own address is read or written: it comes from the
    account, never from the request.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        operation_id="getEmailPreferences",
        summary=_("List the marketing emails you can choose"),
        description=_(
            "The marketing emails this store sends, each with whether your "
            "account's address still receives it."
        ),
        tags=["User Subscriptions"],
        responses={200: EmailPreferencesSerializer},
    )
    def get(self, request):
        return Response(_body(marketing_preferences(request.user.email)))

    @extend_schema(
        operation_id="updateEmailPreferences",
        summary=_("Turn marketing emails on or off"),
        description=_(
            "Declining a type records an opt-out for it. Accepting one lifts "
            "the opt-out that covers it and leaves your other choices as "
            "they are, including after an unsubscribe from all marketing "
            "email. Answers with the same list as the GET."
        ),
        tags=["User Subscriptions"],
        request=EmailPreferencesWriteSerializer,
        responses={
            200: EmailPreferencesSerializer,
            400: ErrorResponseSerializer,
        },
    )
    def patch(self, request):
        serializer = EmailPreferencesWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        choices = {
            choice["key"]: choice["enabled"]
            for choice in serializer.validated_data["types"]
        }
        preferences = set_marketing_preferences(
            request.user.email, account_pk=request.user.pk, choices=choices
        )
        return Response(_body(preferences))
