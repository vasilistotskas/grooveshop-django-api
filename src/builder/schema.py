"""OpenAPI fragments the builder's views share: the tag, the locale and
``If-Match`` parameters and the error bodies an operation can answer.
"""

from __future__ import annotations

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
)

from builder.localization import (
    LOCALE_PARAM,
)
from builder.serializers import (
    BuilderDocumentInvalidErrorSerializer,
    BuilderRevisionConflictSerializer,
)
from core.api.serializers import ErrorResponseSerializer

TAGS = ["Builder"]
LOCALE_OPENAPI_PARAMETER = OpenApiParameter(
    name=LOCALE_PARAM,
    location=OpenApiParameter.QUERY,
    required=False,
    type=str,
    description=(
        "Language to resolve the content in; unknown or omitted means the "
        "store's default language."
    ),
)
ERRORS = {
    400: OpenApiResponse(response=ErrorResponseSerializer),
    409: OpenApiResponse(response=ErrorResponseSerializer),
}
#: Statuses that more than one operation answers, with their plain body.
_PLAIN = OpenApiResponse(response=ErrorResponseSerializer)
NOT_FOUND = {404: _PLAIN}
STORE_UNAVAILABLE = {503: _PLAIN}
#: An operation that takes ``If-Match``: ``428`` without it, and a stale one is
#: the conflict that names the current revision and who made it.
REVISION_ERRORS = {
    409: OpenApiResponse(response=BuilderRevisionConflictSerializer),
    428: _PLAIN,
}
#: For an operation that can answer ``invalid_document`` (it checks a
#: document, a schedule time, a preview or a presence list).
DOCUMENT_INVALID_ERRORS = {
    **ERRORS,
    400: OpenApiResponse(response=BuilderDocumentInvalidErrorSerializer),
}


IF_MATCH = OpenApiParameter(
    name="If-Match",
    location=OpenApiParameter.HEADER,
    required=True,
    type=str,
    description=(
        "The draft revision this change is made against, as read from the "
        "document (`draftRevision`, or the ETag). `0` creates a document "
        "that does not exist. A stale revision answers 409 "
        "`revision_conflict` with the current one and who saved it."
    ),
)
