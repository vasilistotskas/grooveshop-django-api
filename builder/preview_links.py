"""Share-preview links: show unpublished work to someone without an account.

``create`` validates the current drafts of the documents named (the same
check a draft save makes; references are not checked, as on a save) and
freezes them in a ``BuilderPreviewLink``, outside the version history, so a
link never touches numbering, retention or releases. The person gets a
token once; only its SHA-256 is stored, so neither a database copy nor the
admin can open anyone's link.

``open_link`` is the public read behind it: the frozen documents stand in for
the current release's documents of the same keys and the page is resolved by
the same code as the published read (``builder.published.preview``). A token
that is unknown, revoked or past its time is one answer, so a probe learns
nothing about which links ever existed.

The token is 256 random bits (``secrets.token_urlsafe(32)``), so the limit on
open links bounds load and the list, not guessing.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import replace
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from builder import published
from builder.errors import DocumentInvalid, Issue, NotFoundError
from builder.models import BuilderDocument, BuilderPreviewLink
from builder.services import (
    CHANGE_PERMISSION,
    _catalogue,
    _issues,
    require_permission,
)
from core.retention import delete_in_batches
from tenant.staff_actors import staff_id


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _problem(path: str, code: str, message: str) -> DocumentInvalid:
    return DocumentInvalid([Issue(path=path, code=code, message=message)])


def active() -> Any:
    """The links that still open."""
    return BuilderPreviewLink.objects.filter(expires_at__gt=timezone.now())


def create(
    user: Any, *, keys: list[str], note: str = ""
) -> tuple[BuilderPreviewLink, str]:
    """Freeze the current drafts of *keys* behind a new link; returns the
    link and its token, which exists nowhere else."""
    require_permission(user, CHANGE_PERMISSION)
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        raise _problem(
            "/documents",
            "nothing_to_share",
            _("Choose at least one document to share."),
        )
    if len(wanted) > settings.BUILDER_PREVIEW_MAX_DOCUMENTS:
        raise _problem(
            "/documents",
            "too_many_documents",
            _("A preview can carry at most %(limit)s documents.")
            % {"limit": settings.BUILDER_PREVIEW_MAX_DOCUMENTS},
        )
    if active().count() >= settings.BUILDER_MAX_PREVIEW_LINKS:
        raise _problem(
            "",
            "too_many_preview_links",
            _("A store can have at most %(limit)s open preview links.")
            % {"limit": settings.BUILDER_MAX_PREVIEW_LINKS},
        )
    documents = {
        document.key: document
        for document in BuilderDocument.objects.filter(key__in=wanted)
    }
    unknown = [key for key in wanted if key not in documents]
    if unknown:
        raise NotFoundError(
            _("There is no such document: %(keys)s.")
            % {"keys": ", ".join(unknown)}
        )

    catalogue = _catalogue()
    problems: list[Issue] = []
    snapshots: dict[str, dict[str, Any]] = {}
    for key in wanted:
        document = documents[key]
        issues, validated = _issues(
            key, document.draft, document.draft_i18n, catalogue
        )
        problems.extend(replace(issue, document=key) for issue in issues)
        if validated is not None:
            snapshots[key] = {
                "data": validated.data,
                "i18n": validated.i18n,
            }
    if problems:
        raise DocumentInvalid(problems)

    token = secrets.token_urlsafe(32)
    with transaction.atomic():
        link = BuilderPreviewLink.objects.create(
            token_hash=hash_token(token),
            snapshots=snapshots,
            note=note,
            expires_at=timezone.now()
            + timedelta(seconds=settings.BUILDER_PREVIEW_LINK_TTL_SECONDS),
            created_by_staff_id=staff_id(user),
        )
    return link, token


def revoke(user: Any, *, pk: int) -> None:
    """End a link at once. Unknown and already expired are both not found."""
    require_permission(user, CHANGE_PERMISSION)
    deleted, _rows = active().filter(pk=pk).delete()
    if not deleted:
        raise NotFoundError(_("There is no such preview link."))


def open_link(token: str, page_key: str, locale: str) -> dict[str, Any] | None:
    """The page *page_key* with the link's frozen documents over the
    current release, or ``None`` when the page exists in neither. Raises
    ``NotFoundError`` (``preview_link_expired``) for a token that opens
    nothing."""
    link = active().filter(token_hash=hash_token(token)).first()
    if link is None:
        raise NotFoundError(code="preview_link_expired")
    copies = {
        key: published.Source(snapshot["data"], snapshot["i18n"])
        for key, snapshot in link.snapshots.items()
    }
    return published.preview(page_key, locale, copies)


def purge_expired() -> int:
    """Delete the links past their time (``builder.tasks``, per store)."""
    return delete_in_batches(
        BuilderPreviewLink.objects.filter(expires_at__lte=timezone.now())
    )
