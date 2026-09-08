import logging

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from contact.attachments import AttachmentPolicy, storage_pressure
from contact.models import Contact, ContactAttachment, Feedback
from contact.serializers import (
    ContactAttachmentSerializer,
    ContactWriteSerializer,
    FeedbackWriteSerializer,
)
from core.api.serializers import ErrorResponseSerializer
from core.api.throttling import (
    ContactAttachmentThrottle,
    ContactCreateThrottle,
    FeedbackCreateThrottle,
)
from tenant.permissions import IsContactAttachmentsEnabled, IsFeedbackEnabled

logger = logging.getLogger(__name__)


class ContactCreateView(generics.CreateAPIView):
    queryset = Contact.objects.all()
    serializer_class = ContactWriteSerializer
    permission_classes = [AllowAny]
    # Stack: global anon/user daily caps + a tight per-IP burst limit for this
    # unauthenticated endpoint. Without this, the default 100k/day anon limit
    # is too loose for a contact form and enables spam/abuse.
    throttle_classes = [
        AnonRateThrottle,
        UserRateThrottle,
        ContactCreateThrottle,
    ]

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


class FeedbackCreateView(generics.CreateAPIView):
    queryset = Feedback.objects.all()
    serializer_class = FeedbackWriteSerializer
    # Anonymous submissions allowed; the merchant feature gate 404s
    # the endpoint when FEEDBACK_ENABLED is off.
    permission_classes = [IsFeedbackEnabled]
    # Same throttle stack rationale as ContactCreateView above.
    throttle_classes = [
        AnonRateThrottle,
        UserRateThrottle,
        FeedbackCreateThrottle,
    ]

    @extend_schema(
        operation_id="createFeedback",
        summary=_("Create a feedback submission"),
        description=_(
            "Submit storefront feedback (rating, category, message)."
        ),
        tags=["Feedback"],
        responses={
            201: FeedbackWriteSerializer,
            400: ErrorResponseSerializer,
        },
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class ContactAttachmentUploadView(generics.CreateAPIView):
    """Accept ONE file, ahead of the enquiry that will claim it.

    Two steps rather than one multipart submit, because the parts have
    different shapes: a file is big, slow and worth showing progress
    for, while the enquiry is small and must not be re-sent because the
    third upload failed. The visitor picks files, watches them land,
    then submits; the response's ``uuid`` is what the submit spends to
    claim each one (``ContactWriteSerializer.attachment_ids``).

    Anonymous by necessity — a contact form has no login — so the
    endpoint is gated three ways: the merchant's own switch (404 when
    off, and off is the default), a per-caller throttle, and the
    serializer's size/type checks. What the bytes CANNOT do is be
    served back: they land in the private tree, and the only reader is
    the admin download.
    """

    queryset = ContactAttachment.objects.all()
    serializer_class = ContactAttachmentSerializer
    # The gate 404s the route unless the store turned attachments on;
    # replacing the default IsAuthenticatedOrReadOnly is what makes the
    # POST anonymous, as on FeedbackCreateView.
    permission_classes = [IsContactAttachmentsEnabled]
    # Only multipart: a base64 JSON body would put the whole file in
    # the request parser's memory and defeat Django's temporary-file
    # upload handler.
    parser_classes = [MultiPartParser]
    throttle_classes = [
        AnonRateThrottle,
        UserRateThrottle,
        ContactAttachmentThrottle,
    ]

    #: Slack over the per-file ceiling for the multipart envelope:
    #: the boundary, the part headers and the filename. Generous,
    #: because this check exists to refuse an obviously-wrong body,
    #: not to enforce the limit — the serializer does that from the
    #: bytes it actually counts.
    ENVELOPE_SLACK = 64 * 1024

    @extend_schema(
        operation_id="createContactAttachment",
        summary=_("Upload a contact-form attachment"),
        description=_(
            "Upload one file to attach to a contact enquiry. Returns "
            "the id to send back in the enquiry's attachmentIds. Only "
            "available while the store accepts attachments."
        ),
        tags=["Contact"],
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {
                    "file": {"type": "string", "format": "binary"},
                },
                "required": ["file"],
            }
        },
        responses={
            201: ContactAttachmentSerializer,
            400: ErrorResponseSerializer,
            404: ErrorResponseSerializer,
            413: ErrorResponseSerializer,
            503: ErrorResponseSerializer,
        },
    )
    def post(self, request, *args, **kwargs):
        """Refuse the impossible and the untimely, then accept.

        Both checks read the DECLARED ``Content-Length`` and run
        BEFORE ``request.data``, which is the point of them. Touching
        ``request.data`` runs Django's multipart parser, and that
        writes a SECOND copy of the body to the pod's disk — so a
        refusal that arrives after it has already cost what accepting
        would have cost. (The first copy is Django's own ASGI body
        spool and happens before any application code; bounding THAT
        is the ingress's job, and this endpoint is why the ingress
        needs a limit.)

        413 when the body cannot possibly be one acceptable file: no
        store can raise ``MAX_BYTES_CEILING``, so this is a fact about
        the request rather than about the store.

        503 when a per-tenant ceiling is reached: nothing is wrong
        with the file, the store is temporarily out of room, and the
        sweeps clear it. The per-caller throttle above bounds ONE
        visitor; ``storage_pressure`` is what bounds all of them at
        once, on a volume that also holds the invoices.
        """
        declared = int(request.headers.get("content-length") or 0)
        if declared > AttachmentPolicy.MAX_BYTES_CEILING + self.ENVELOPE_SLACK:
            return Response(
                {"detail": _("That file is too large.")},
                status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            )
        if storage_pressure(declared) is not None:
            return Response(
                {
                    "detail": _(
                        "Files cannot be accepted right now. Please try "
                        "again in a few minutes."
                    )
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return super().post(request, *args, **kwargs)
