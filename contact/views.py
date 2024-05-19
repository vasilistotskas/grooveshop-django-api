from rest_framework import generics
import logging


from contact.models import Contact
from contact.serializers import ContactSerializer
from core.api.throttling import BurstRateThrottle

logger = logging.getLogger(__name__)


class ContactCreateView(generics.CreateAPIView):
    queryset = Contact.objects.all()
    serializer_class = ContactSerializer
    throttle_classes = [BurstRateThrottle]

