import logging

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import generics
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from contact.models import Contact
from contact.serializers import ContactWriteSerializer
from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import ContactCreateThrottle

logger = logging.getLogger(__name__)


class ContactCreateView(generics.CreateAPIView):
    queryset = Contact.objects.all()
    serializer_class = ContactWriteSerializer
    permission_classes = [AllowAny]
    # Stack: global anon/user daily caps + a tight per-IP burst limit for this
    # unauthenticated endpoint. Without this, the default 100k/day anon limit
    # is too loose for a contact form and enables spam/abuse.
    throttle_classes = [AnonRateThrottle, UserRateThrottle, ContactCreateThrottle]

    @extend_schema(
        operation_id="createContact",
        summary=_("Create a contact message"),
        description=_("Send a contact message to the site administrators."),
        tags=["Contact"],
        responses={
            201: ContactWriteSerializer,
            400: ErrorResponseSerializer,
        },
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)
