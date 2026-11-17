"""What every staff route has in common: the renderer, parser and
permission, the ``If-Match`` revision and the ``ETag`` of a document.
"""

from __future__ import annotations

from typing import Any

from django.db.models import QuerySet
from django.utils.translation import gettext as _
from rest_framework.generics import ListAPIView
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from builder.errors import (
    BuilderError,
    PreconditionRequired,
)
from builder.models import (
    BuilderDocument,
)
from builder.rendering import (
    BuilderJSONParser,
    BuilderJSONRenderer,
)
from core.api.permissions import StoreStaffModelPermissions


def expected_revision(request: Request) -> int:
    """The revision named in ``If-Match`` (``"3"``, ``W/"3"`` or ``3``)."""
    raw = request.headers.get("If-Match")
    if raw is None:
        raise PreconditionRequired()
    value = raw.strip().removeprefix("W/").strip('"')
    if not value.isdigit():
        raise BuilderError(
            _("If-Match must be a revision number."), code="invalid_revision"
        )
    return int(value)


def etagged(response: Response, document: BuilderDocument) -> Response:
    response["ETag"] = f'"{document.draft_revision}"'
    return response


class StaffView(APIView):
    """Common to every staff route."""

    renderer_classes = [BuilderJSONRenderer]
    parser_classes = [BuilderJSONParser]
    permission_classes = [StoreStaffModelPermissions]
    queryset: QuerySet[Any] = BuilderDocument.objects.none()


class StaffListView(ListAPIView):
    renderer_classes = [BuilderJSONRenderer]
    permission_classes = [StoreStaffModelPermissions]
