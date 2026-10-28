"""Every write of a store's text overrides.

One path for the editor's save, reset and restore (and for anything that
comes later): permission first, then validation against the storefront,
then the write. The writes go through ``Model.save``/``delete`` so the
history row, the store's cache generation and the storefront purge
(``texts`` cache surface) all follow, with nothing to remember here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.http import Http404
from django.utils.translation import gettext

from texts import storefront
from texts.models import TextOverride, TextSurface
from texts.validation import (
    UNSUPPORTED_LANGUAGE,
    check_value,
    normalise,
    refusal,
)

CHANGE_PERMISSION = "texts.change_textoverride"


def require_change_permission(user) -> None:
    if not user.has_perm(CHANGE_PERMISSION):
        raise PermissionDenied


def _schema() -> str:
    return connection.schema_name


def _rows(namespace: str, key: str) -> dict[str, TextOverride]:
    return {
        row.language: row
        for row in TextOverride.objects.filter(
            surface=TextSurface.STOREFRONT, namespace=namespace, key=key
        )
    }


@dataclass(frozen=True)
class SaveResult:
    """What a save did, per language."""

    errors: dict[str, ValidationError]
    saved: tuple[str, ...] = ()
    reset: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


def _write(user, row: TextOverride, value: str, reason: str) -> None:
    row.value = value
    row.updated_by_staff = user
    row._history_user = user
    row._change_reason = reason
    row.save()


def _delete(user, row: TextOverride, reason: str) -> None:
    row._history_user = user
    row._change_reason = reason
    row.delete()


def save_texts(
    user,
    *,
    namespace: str,
    key: str,
    values: Mapping[str, str],
) -> SaveResult:
    """Store *values* (language -> text) for one text.

    A value equal to the default, after normalisation, removes the
    override. All or nothing: when any language is refused nothing is
    written, and ``errors`` names each refused language.
    """
    require_change_permission(user)
    schema = _schema()
    try:
        catalogue = storefront.fetch_catalogue(schema)
    except storefront.StorefrontUnavailable as exc:
        raise refusal("storefront_unavailable") from exc
    entry = catalogue.get(namespace, key)
    existing = _rows(namespace, key)

    errors: dict[str, ValidationError] = {}
    to_store: dict[str, str] = {}
    to_reset: list[str] = []
    for language, raw in values.items():
        if language not in catalogue.locales:
            errors[language] = refusal(UNSUPPORTED_LANGUAGE, language=language)
            continue
        default = entry.defaults.get(language) if entry else None
        wanted = normalise(raw)
        if default is not None and wanted == default:
            if language in existing:
                to_reset.append(language)
            continue
        current = existing.get(language)
        if current is not None and wanted == current.value:
            continue
        if not wanted and current is None and default is None:
            # A language the text has no default in, left empty.
            continue
        try:
            to_store[language] = check_value(
                namespace=namespace,
                key=key,
                language=language,
                value=raw,
                schema=schema,
            )
        except ValidationError as error:
            errors[language] = error
    if errors:
        return SaveResult(errors=errors)

    with transaction.atomic():
        for language, value in to_store.items():
            row = existing.get(language) or TextOverride(
                surface=TextSurface.STOREFRONT,
                namespace=namespace,
                key=key,
                language=language,
            )
            _write(user, row, value, gettext("Edited"))
        for language in to_reset:
            _delete(user, existing[language], gettext("Reset to default"))
    return SaveResult(errors={}, saved=tuple(to_store), reset=tuple(to_reset))


def reset_text(user, *, namespace: str, key: str) -> int:
    """Remove every override of one text; returns how many."""
    require_change_permission(user)
    rows = list(_rows(namespace, key).values())
    with transaction.atomic():
        for row in rows:
            _delete(user, row, gettext("Reset to default"))
    return len(rows)


def restore_text(user, *, history_id: int) -> str:
    """Put a text back as it was after history entry *history_id*.

    The value is validated again: the storefront may have changed since.
    Returns the language restored.
    """
    require_change_permission(user)
    entry = TextOverride.history.filter(
        history_id=history_id, surface=TextSurface.STOREFRONT
    ).first()
    if entry is None:
        raise Http404
    row = TextOverride.objects.filter(
        surface=entry.surface,
        namespace=entry.namespace,
        key=entry.key,
        language=entry.language,
    ).first()
    reason = gettext("Restored")
    if entry.history_type == "-":
        if row is not None:
            _delete(user, row, reason)
        return entry.language
    value = check_value(
        namespace=entry.namespace,
        key=entry.key,
        language=entry.language,
        value=entry.value,
        schema=_schema(),
    )
    with transaction.atomic():
        _write(
            user,
            row
            or TextOverride(
                surface=entry.surface,
                namespace=entry.namespace,
                key=entry.key,
                language=entry.language,
            ),
            value,
            reason,
        )
    return entry.language
