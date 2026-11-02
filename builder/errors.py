"""What the builder refuses, with the stable code a client branches on.

Every error body is ``{detail, code, ...extra}`` (``docs/error-contract.md``).
The extra keys are plain JSON (a revision is a number, not a string), so
``detail`` is set after ``APIException`` has run, which would otherwise
turn every value into a message.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import status
from rest_framework.exceptions import APIException


class BuilderError(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "invalid"
    default_detail = _("The request could not be processed.")

    def __init__(
        self,
        detail: Any = None,
        *,
        code: str | None = None,
        **extra: Any,
    ) -> None:
        super().__init__(detail, code)
        message = self.detail if isinstance(self.detail, str) else detail
        self.detail = {  # ty: ignore[invalid-assignment]
            "detail": str(message),
            "code": code or self.default_code,
            **extra,
        }


class StorefrontUnavailableError(BuilderError):
    """No catalogue to check the save against, so nothing is saved."""

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "storefront_unavailable"
    default_detail = _(
        "The storefront cannot be reached, so the change cannot be checked. "
        "Try again in a moment."
    )


class RevisionConflict(BuilderError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "revision_conflict"
    default_detail = _(
        "Someone else changed this document since you opened it."
    )


class PreconditionRequired(BuilderError):
    status_code = status.HTTP_428_PRECONDITION_REQUIRED
    default_code = "precondition_required"
    default_detail = _("Send the revision you are changing in If-Match.")


class InvalidLinkTargets(BuilderError):
    default_code = "invalid_link_targets"
    default_detail = _("The ids to resolve are not valid.")


class InvalidKind(BuilderError):
    default_code = "invalid_kind"
    default_detail = _("The kind of document is not valid.")


class NotFoundError(BuilderError):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = "not_found"
    default_detail = _("Not found.")


@dataclass(frozen=True, slots=True)
class Issue:
    """One thing wrong with a document: where, what kind, and a sentence.

    ``path`` is a JSON pointer into what the client sent (``/sections/
    hero_1/settings/title``); ``document`` names the document when a
    publish checks several.
    """

    path: str
    code: str
    message: str
    document: str | None = None
    params: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "path": self.path,
            "code": self.code,
            "message": self.message,
        }
        if self.document is not None:
            body["document"] = self.document
        if self.params is not None:
            body["params"] = self.params
        return body


#: The sentences of the problems a scheduled publish can be left with, by
#: code. The message is written when the sweep runs, in whatever language
#: that process has; the email re-reads it from the code and the params in
#: the language it is sent in (``message_for``).
FAILURE_MESSAGES = {
    "reference_not_found": _(
        "%(kind)s %(target)s does not exist in this store."
    ),
    "document_gone": _("%(key)s no longer exists."),
    "nothing_to_publish": _("Nothing has changed since the last release."),
}


def message_for(code: str, params: dict[str, str] | None, fallback: str) -> str:
    """The sentence for *code* in the active language, else *fallback*."""
    template = FAILURE_MESSAGES.get(code)
    if template is None or (params is None and "%(" in str(template)):
        return fallback
    return str(template) % (params or {})


class DocumentInvalid(BuilderError):
    default_code = "invalid_document"
    default_detail = _("The document has problems that must be fixed first.")

    def __init__(self, issues: list[Issue]) -> None:
        super().__init__(errors=[issue.as_dict() for issue in issues])
        self.issues = issues
