import logging

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import generics

from contact.models import Contact
from contact.serializers import ContactWriteSerializer
from core.api.serializers import ErrorResponseSerializer

logger = logging.getLogger(__name__)


class ContactCreateView(generics.CreateAPIView):
    queryset = Contact.objects.all()
    serializer_class = ContactWriteSerializer

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
