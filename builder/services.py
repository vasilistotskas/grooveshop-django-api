"""Every write of the page builder's data.

One path for the editor's save, checkpoint, restore, discard, publish and
rollback: permission first, then validation against the storefront's
catalogue (refused when the storefront cannot answer), then the write. The
release is the one thing that reaches the public storefront, so creating
one is what moves the store's cache generation and the ``builder`` cache
surface (``tenant.signals``, ``core.cache.surfaces``); a draft save purges
nothing, because no public read touches a draft.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from typing import Any

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, connection, transaction
from django.db.models import Max
from django.utils.translation import gettext as _

from builder import (
    presence,
    published,
    references,
    storefront,
    validation,
)
from builder.errors import (
    DocumentInvalid,
    Issue,
    NotFoundError,
    RevisionConflict,
    StorefrontUnavailableError,
)
from builder.models import (
    BuilderDocument,
    BuilderRelease,
    BuilderScheduledPublish,
    BuilderVersion,
    DocumentKind,
    ReferenceKind,
    ScheduleStatus,
    VersionKind,
    empty_document,
    kind_of_key,
)
from core.storefront import StorefrontUnavailable
from tenant.features import feature_on
from tenant.staff_actors import staff_id

VIEW_PERMISSION = "builder.view_builderdocument"
CHANGE_PERMISSION = "builder.change_builderdocument"
PUBLISH_PERMISSION = "builder.add_builderrelease"

#: ``If-Match`` of a document that does not exist yet.
NEW_DOCUMENT_REVISION = 0


def require_permission(user: Any, permission: str) -> None:
    if not user.has_perm(permission):
        raise PermissionDenied


def staff_summary(actor: Any) -> dict[str, Any] | None:
    """``{id, name}`` of a staff member for a response, or ``None``."""
    if actor is None:
        return None
    name = (
        actor.get_full_name().strip() if hasattr(actor, "get_full_name") else ""
    )
    return {"id": actor.pk, "name": name or actor.get_username()}


def _catalogue() -> storefront.Catalogue:
    try:
        return storefront.fetch_catalogue(connection.schema_name)
    except StorefrontUnavailable as exc:
        raise StorefrontUnavailableError() from exc


def _issues(
    key: str, data: Any, i18n: Any, catalogue: storefront.Catalogue
) -> tuple[list[Issue], validation.Validated | None]:
    return validation.validate_document(
        key=key,
        data=data,
        i18n=i18n,
        catalogue=catalogue,
        feature_on=feature_on,
    )


def _validated(
    key: str, data: Any, i18n: Any, catalogue: storefront.Catalogue
) -> validation.Validated:
    issues, validated = _issues(key, data, i18n, catalogue)
    if validated is None:
        raise DocumentInvalid(issues)
    return validated


def preview(
    user: Any,
    *,
    page_key: str,
    locale: str,
    copies: dict[str, tuple[Any, Any]],
) -> dict[str, Any] | None:
    """The page *page_key* as the public read would answer it if the
    editor's unsaved working copies *copies* (``{key: (draft, i18n)}``)
    were released now, resolved through ``published.preview``. Each copy is
    validated like a draft save (references are not checked, as on a save)
    and nothing is stored. ``None`` when the page does not exist."""
    require_permission(user, VIEW_PERMISSION)
    if len(copies) > settings.BUILDER_PREVIEW_MAX_DOCUMENTS:
        raise DocumentInvalid(
            [
                Issue(
                    path="/documents",
                    code="too_many_documents",
                    message=_(
                        "A preview can carry at most %(limit)s documents."
                    )
                    % {"limit": settings.BUILDER_PREVIEW_MAX_DOCUMENTS},
                )
            ]
        )
    sources: dict[str, published.Source] = {}
    if copies:
        catalogue = _catalogue()
        problems: list[Issue] = []
        for key, (draft, i18n) in copies.items():
            issues, validated = _issues(key, draft, i18n, catalogue)
            problems.extend(replace(issue, document=key) for issue in issues)
            if validated is not None:
                sources[key] = published.Source(validated.data, validated.i18n)
        if problems:
            raise DocumentInvalid(problems)
    return published.preview(page_key, locale, sources)


def heartbeat(
    user: Any, *, documents: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Record that *user* has *documents* open and answer who else has
    each of them (``builder.presence``). Any role that may look may say so."""
    require_permission(user, VIEW_PERMISSION)
    actor = staff_summary(user)
    assert actor is not None  # an authenticated staff identity
    return presence.heartbeat(
        staff=actor["id"], name=actor["name"], documents=documents
    )


def leave_presence(user: Any) -> None:
    require_permission(user, VIEW_PERMISSION)
    presence.leave(user.pk)


def current_release() -> BuilderRelease | None:
    return BuilderRelease.objects.order_by("-number").first()


def published_versions() -> dict[str, int]:
    release = current_release()
    return dict(release.versions) if release else {}


def published_numbers() -> dict[str, int]:
    """``{document key: version number}`` of the current release."""
    ids = published_versions()
    numbers = dict(
        BuilderVersion.objects.filter(pk__in=ids.values()).values_list(
            "pk", "number"
        )
    )
    return {key: numbers[pk] for key, pk in ids.items() if pk in numbers}


def _locked(key: str) -> BuilderDocument:
    document = (
        BuilderDocument.objects.select_for_update().filter(key=key).first()
    )
    if document is None:
        raise NotFoundError(_("There is no such document."))
    return document


def _conflict(document: BuilderDocument) -> RevisionConflict:
    return RevisionConflict(
        current_revision=document.draft_revision,
        updated_by=staff_summary(document.updated_by_staff),
        updated_at=document.updated_at.isoformat(),
    )


def _check_revision(document: BuilderDocument, expected: int) -> None:
    if document.draft_revision != expected:
        raise _conflict(document)


def _apply_draft(
    user: Any,
    document: BuilderDocument,
    *,
    data: dict[str, Any],
    i18n: dict[str, Any],
    catalogue_hash: str,
) -> None:
    """Set the draft and move its revision; the caller saves."""
    document.draft = data
    document.draft_i18n = i18n
    document.draft_catalogue_hash = catalogue_hash
    document.draft_revision += 1
    document.updated_by_staff = user


def _create_document(key: str, kind: DocumentKind) -> BuilderDocument:
    """A new document; a deleted page of that key is brought back."""
    deleted = (
        BuilderDocument.objects.deleted_only()
        .select_for_update()
        .filter(key=key)
    ).first()
    if deleted is not None:
        deleted.restore()
        deleted.draft = empty_document()
        deleted.draft_i18n = {}
        return deleted
    if (
        kind is DocumentKind.PAGE
        and BuilderDocument.objects.filter(kind=DocumentKind.PAGE).count()
        >= settings.BUILDER_MAX_PAGES
    ):
        raise DocumentInvalid(
            [
                Issue(
                    path="",
                    code="too_many_pages",
                    message=_("A store can have at most %(limit)s pages.")
                    % {"limit": settings.BUILDER_MAX_PAGES},
                )
            ]
        )
    try:
        with transaction.atomic():
            return BuilderDocument.objects.create(
                key=key, kind=kind, draft_revision=NEW_DOCUMENT_REVISION
            )
    except IntegrityError:
        raise _conflict(_locked(key)) from None


def save_draft(
    user: Any,
    *,
    key: str,
    draft: Any,
    i18n: Any,
    expected_revision: int,
) -> BuilderDocument:
    """Store the editor's working copy of document *key*.

    *expected_revision* is the revision the editor loaded
    (``NEW_DOCUMENT_REVISION`` for a document that does not exist yet); a
    save made against another revision is refused with the current one and
    who saved it. The document is validated in full, so every draft is
    one the editor could publish apart from the references (checked at
    publish: a product deleted since is not the merchant's mistake).
    Saving what is already stored changes nothing, so a repeated autosave
    does not move the revision.
    """
    require_permission(user, CHANGE_PERMISSION)
    kind = kind_of_key(key)
    catalogue = _catalogue()
    validated = _validated(key, draft, i18n, catalogue)
    assert kind is not None  # an invalid key is an issue above

    with transaction.atomic():
        existing = (
            BuilderDocument.objects.select_for_update().filter(key=key).first()
        )
        if existing is None:
            if expected_revision != NEW_DOCUMENT_REVISION:
                raise NotFoundError(_("There is no such document."))
            document = _create_document(key, kind)
        elif expected_revision == NEW_DOCUMENT_REVISION:
            raise _conflict(existing)
        else:
            document = existing
            _check_revision(document, expected_revision)
            if validation.encode(
                document.draft, document.draft_i18n
            ) == validation.encode(validated.data, validated.i18n):
                return document
        _apply_draft(
            user,
            document,
            data=validated.data,
            i18n=validated.i18n,
            catalogue_hash=catalogue.hash,
        )
        document.save()
    return document


def _new_version(
    user: Any,
    document: BuilderDocument,
    validated: validation.Validated,
    *,
    kind: VersionKind,
    note: str,
    catalogue_hash: str,
) -> BuilderVersion:
    """The version holding *validated*: the newest one when it already
    holds exactly this content, else a new one. The document row is
    locked by the caller, so numbers cannot collide."""
    latest = document.versions.order_by("-number").first()
    if latest is not None and latest.content_hash == validated.content_hash:
        return latest
    return BuilderVersion.objects.create(
        document=document,
        number=(latest.number if latest else 0) + 1,
        kind=kind,
        data=validated.data,
        i18n=validated.i18n,
        block_types=validated.block_types,
        refs=validated.refs,
        catalogue_hash=catalogue_hash,
        content_hash=validated.content_hash,
        size_bytes=validated.size_bytes,
        created_by_staff_id=staff_id(user),
        note=note,
    )


def prune_unreleased_checkpoints(document: BuilderDocument) -> int:
    """Delete the oldest named saves and restores no release points at,
    keeping ``BUILDER_UNRELEASED_CHECKPOINTS_KEPT``. Returns how many
    went."""
    released = {
        int(version_id)
        for versions in BuilderRelease.objects.values_list(
            "versions", flat=True
        )
        for version_id in versions.values()
    }
    released |= {
        int(version_id)
        for versions in BuilderScheduledPublish.objects.filter(
            status=ScheduleStatus.PENDING
        ).values_list("versions", flat=True)
        for version_id in versions.values()
    }
    stale = list(
        BuilderVersion.objects.filter(
            document=document,
            kind__in=(VersionKind.CHECKPOINT, VersionKind.RESTORE),
        )
        .exclude(pk__in=released)
        .order_by("-number")
        .values_list("pk", flat=True)[
            settings.BUILDER_UNRELEASED_CHECKPOINTS_KEPT :
        ]
    )
    if not stale:
        return 0
    return BuilderVersion.objects.filter(pk__in=stale).delete()[0]


def create_checkpoint(user: Any, *, key: str, note: str = "") -> BuilderVersion:
    """A named save: freeze the current draft as a version. Autosaves
    never make one."""
    require_permission(user, CHANGE_PERMISSION)
    catalogue = _catalogue()
    with transaction.atomic():
        document = _locked(key)
        validated = _validated(
            key, document.draft, document.draft_i18n, catalogue
        )
        version = _new_version(
            user,
            document,
            validated,
            kind=VersionKind.CHECKPOINT,
            note=note,
            catalogue_hash=catalogue.hash,
        )
        prune_unreleased_checkpoints(document)
    return version


def restore_version(
    user: Any, *, key: str, number: int, expected_revision: int
) -> BuilderDocument:
    """Put version *number* back as the DRAFT, and record it as a new
    version of kind ``restore`` that names *number* (``restored_from``), so
    the history can say "restored from version N". When the newest version
    already holds that content nothing new is recorded, like a named save of
    unchanged content. It is not checked against the catalogue: the
    storefront may have moved on since, and the editor upgrades old blocks
    when it opens the draft; publishing validates."""
    require_permission(user, CHANGE_PERMISSION)
    with transaction.atomic():
        document = _locked(key)
        _check_revision(document, expected_revision)
        version = BuilderVersion.objects.filter(
            document=document, number=number
        ).first()
        if version is None:
            raise NotFoundError(_("There is no such version."))
        _apply_draft(
            user,
            document,
            data=version.data,
            i18n=version.i18n,
            catalogue_hash=version.catalogue_hash,
        )
        document.save()
        latest = document.versions.order_by("-number").first()
        if latest is None or latest.content_hash != version.content_hash:
            BuilderVersion.objects.create(
                document=document,
                number=(latest.number if latest else 0) + 1,
                kind=VersionKind.RESTORE,
                data=version.data,
                i18n=version.i18n,
                block_types=version.block_types,
                refs=version.refs,
                catalogue_hash=version.catalogue_hash,
                content_hash=version.content_hash,
                size_bytes=version.size_bytes,
                created_by_staff_id=staff_id(user),
                restored_from=version.number,
            )
            prune_unreleased_checkpoints(document)
    return document


def discard_draft(
    user: Any, *, key: str, expected_revision: int
) -> BuilderDocument:
    """Throw the draft away: it becomes what is published (empty for a
    document never published)."""
    require_permission(user, CHANGE_PERMISSION)
    with transaction.atomic():
        document = _locked(key)
        _check_revision(document, expected_revision)
        version_id = published_versions().get(key)
        version = BuilderVersion.objects.filter(pk=version_id).first()
        _apply_draft(
            user,
            document,
            data=version.data if version else empty_document(),
            i18n=version.i18n if version else {},
            catalogue_hash=version.catalogue_hash if version else "",
        )
        document.published_revision = (
            document.draft_revision if version else None
        )
        document.save()
    return document


def _lock_release_sequence() -> None:
    """Serialise release creation per store: two publishes of different
    documents would otherwise both take the next number."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"builder_release:{connection.schema_name}"],
        )


def _create_release(
    staff: int | None,
    versions: dict[str, int],
    *,
    note: str,
    rolled_back_from: BuilderRelease | None = None,
) -> BuilderRelease:
    release = BuilderRelease.objects.create(
        number=(BuilderRelease.objects.aggregate(top=Max("number"))["top"] or 0)
        + 1,
        versions=versions,
        published_by_staff_id=staff,
        note=note,
        rolled_back_from=rolled_back_from,
    )
    references.rebuild_index(versions)
    return release


@dataclass(frozen=True, slots=True)
class PublishResult:
    release: BuilderRelease
    changed: tuple[str, ...]


def _publish_documents(
    keys: Sequence[str], *, lock: bool = True
) -> list[BuilderDocument]:
    queryset = BuilderDocument.objects.filter(key__in=keys).order_by("key")
    documents = list(queryset.select_for_update() if lock else queryset)
    unknown = sorted(set(keys) - {document.key for document in documents})
    if unknown:
        raise NotFoundError(
            _("There is no such document: %(keys)s.")
            % {"keys": ", ".join(unknown)}
        )
    return documents


def validated_drafts(
    keys: Sequence[str],
    catalogue: storefront.Catalogue,
    *,
    lock: bool = True,
) -> tuple[list[BuilderDocument], dict[str, validation.Validated]]:
    """The locked documents of *keys* with every draft validated in full,
    references included. One problem anywhere is a ``DocumentInvalid``
    listing every problem with its document. The caller holds the
    transaction; ``lock=False`` reads without taking the row locks, for a
    check that writes nothing."""
    documents = _publish_documents(keys, lock=lock)
    problems: list[Issue] = []
    checked: dict[str, validation.Validated] = {}
    for document in documents:
        issues, validated = _issues(
            document.key, document.draft, document.draft_i18n, catalogue
        )
        if validated is not None:
            issues = validation.check_before_release(
                document.key, validated, catalogue
            )
            checked[document.key] = validated
        problems.extend(
            replace(issue, document=document.key) for issue in issues
        )
    if problems:
        raise DocumentInvalid(problems)
    return documents, checked


@dataclass(frozen=True, slots=True)
class PublishCheck:
    """What a publish of some documents would do, found without doing it."""

    changed: tuple[str, ...]
    problems: tuple[Issue, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def check_publish(user: Any, *, keys: Iterable[str]) -> PublishCheck:
    """What ``publish`` of *keys* would answer, writing nothing: every
    problem it would report (with its document), and the documents whose
    draft differs from the version the current release holds. Same
    permission as the publish; takes no lock, so it never holds up a save.
    An unknown document is still a ``NotFoundError``."""
    require_permission(user, PUBLISH_PERMISSION)
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        return PublishCheck(
            (),
            (
                Issue(
                    path="/documents",
                    code="nothing_to_publish",
                    message=_("Choose at least one document to publish."),
                ),
            ),
        )
    catalogue = _catalogue()
    problems: list[Issue] = []
    checked: dict[str, validation.Validated] = {}
    with transaction.atomic():
        documents = _publish_documents(wanted, lock=False)
        for document in documents:
            issues, validated = _issues(
                document.key, document.draft, document.draft_i18n, catalogue
            )
            if validated is not None:
                issues = validation.check_before_release(
                    document.key, validated, catalogue
                )
                checked[document.key] = validated
            problems.extend(
                replace(issue, document=document.key) for issue in issues
            )
        held = dict(
            BuilderVersion.objects.filter(
                pk__in=published_versions().values()
            ).values_list("pk", "content_hash")
        )
        previous = published_versions()
    changed = tuple(
        key
        for key in wanted
        if key in checked
        and held.get(previous.get(key, 0)) != checked[key].content_hash
    )
    if not problems and not changed:
        problems.append(
            Issue(
                path="/documents",
                code="nothing_to_publish",
                message=_("Nothing has changed since the last release."),
            )
        )
    return PublishCheck(changed, tuple(problems))


def release_documents(
    releases: Sequence[BuilderRelease],
) -> dict[int, list[dict[str, Any]]]:
    """``{release id: [{key, version_number, changed}]}``: each document a
    release holds, at which version number, and whether it differs from the
    release before it (the first release changes everything). Two queries
    for any number of releases."""
    numbers = {release.number for release in releases}
    before = {
        release.number: release
        for release in BuilderRelease.objects.filter(
            number__in={n - 1 for n in numbers}
        )
    }
    version_ids = {
        version_id
        for release in (*releases, *before.values())
        for version_id in release.versions.values()
    }
    version_numbers = dict(
        BuilderVersion.objects.filter(pk__in=version_ids).values_list(
            "pk", "number"
        )
    )
    answer: dict[int, list[dict[str, Any]]] = {}
    for release in releases:
        earlier = before.get(release.number - 1)
        held = earlier.versions if earlier is not None else {}
        answer[release.pk] = [
            {
                "key": key,
                "version_number": version_numbers[version_id],
                "changed": held.get(key) != version_id,
            }
            for key, version_id in sorted(release.versions.items())
            if version_id in version_numbers
        ]
    return answer


def publish(user: Any, *, keys: Iterable[str], note: str = "") -> PublishResult:
    """Release the drafts of *keys* together, or none of them.

    Every draft is validated in full, references included; one problem in
    one document stops the whole publish and every problem is reported
    with its document. The release is the previous one with these keys
    pointed at their new versions, so what is not named stays as it was.
    """
    require_permission(user, PUBLISH_PERMISSION)
    wanted = list(dict.fromkeys(keys))
    if not wanted:
        raise DocumentInvalid(
            [
                Issue(
                    path="/documents",
                    code="nothing_to_publish",
                    message=_("Choose at least one document to publish."),
                )
            ]
        )
    catalogue = _catalogue()
    with transaction.atomic():
        _lock_release_sequence()
        documents, checked = validated_drafts(wanted, catalogue)

        previous = published_versions()
        versions = dict(previous)
        for document in documents:
            version = _new_version(
                user,
                document,
                checked[document.key],
                kind=VersionKind.PUBLISH,
                note=note,
                catalogue_hash=catalogue.hash,
            )
            versions[document.key] = version.pk
        changed = tuple(
            key for key in wanted if versions[key] != previous.get(key)
        )
        if not changed:
            raise DocumentInvalid(
                [
                    Issue(
                        path="/documents",
                        code="nothing_to_publish",
                        message=_(
                            "Nothing has changed since the last release."
                        ),
                    )
                ]
            )
        release = _create_release(staff_id(user), versions, note=note)
        for document in documents:
            document.published_revision = document.draft_revision
            document.save(update_fields=["published_revision"])
    return PublishResult(release=release, changed=changed)


def publish_version(
    user: Any, *, key: str, number: int, note: str = ""
) -> BuilderRelease:
    """Release version *number* of document *key* alone: the previous
    release with that one key repointed, so every other document stays as
    it is. The draft is not touched, so work in progress survives; it only
    counts as published when it is exactly this version.

    The version was valid when it was made (a checkpoint is validated, a
    published one was released), and the storefront upgrades old blocks
    when it renders, so it is not checked against the catalogue again; its
    references are, as for any publish."""
    require_permission(user, PUBLISH_PERMISSION)
    with transaction.atomic():
        _lock_release_sequence()
        document = _locked(key)
        version = BuilderVersion.objects.filter(
            document=document, number=number
        ).first()
        if version is None:
            raise NotFoundError(_("There is no such version."))
        previous = published_versions()
        if previous.get(key) == version.pk:
            raise DocumentInvalid(
                [
                    Issue(
                        path="/version",
                        code="nothing_to_publish",
                        message=_("That version is already the published one."),
                        document=key,
                    )
                ]
            )
        problems = validation.check_references(version.refs)
        if problems:
            raise DocumentInvalid(
                [replace(issue, document=key) for issue in problems]
            )
        release = _create_release(
            staff_id(user), {**previous, key: version.pk}, note=note
        )
        is_draft = validation.encode(
            document.draft, document.draft_i18n
        ) == validation.encode(version.data, version.i18n)
        document.published_revision = (
            document.draft_revision if is_draft else None
        )
        document.save(update_fields=["published_revision"])
    return release


def rollback(user: Any, *, release_id: int, note: str = "") -> BuilderRelease:
    """A new release pointing at the versions of an older one. History is
    never rewritten, and the older versions need no validation: they were
    valid when they were released."""
    require_permission(user, PUBLISH_PERMISSION)
    with transaction.atomic():
        _lock_release_sequence()
        target = BuilderRelease.objects.filter(pk=release_id).first()
        if target is None:
            raise NotFoundError(_("There is no such release."))
        if dict(target.versions) == published_versions():
            raise DocumentInvalid(
                [
                    Issue(
                        path="/release",
                        code="nothing_to_publish",
                        message=_("That release is already the current one."),
                    )
                ]
            )
        release = _create_release(
            staff_id(user),
            dict(target.versions),
            note=note,
            rolled_back_from=target,
        )
        BuilderDocument.objects.filter(key__in=target.versions).update(
            published_revision=None
        )
    return release


def delete_page(user: Any, *, key: str, expected_revision: int) -> None:
    """Delete a page. One that is released leaves the store with a new
    release, in the same transaction; a page another released document
    links to is refused."""
    require_permission(user, CHANGE_PERMISSION)
    if kind_of_key(key) is not DocumentKind.PAGE:
        raise DocumentInvalid(
            [
                Issue(
                    path="",
                    code="not_a_page",
                    message=_("Only a page can be deleted."),
                )
            ]
        )
    with transaction.atomic():
        _lock_release_sequence()
        document = _locked(key)
        _check_revision(document, expected_revision)
        slug = key.removeprefix(f"{DocumentKind.PAGE.value}:")
        users = [
            used
            for used in references.used_by(ReferenceKind.PAGE, slug)
            if used != key
        ]
        if users:
            raise DocumentInvalid(
                [
                    Issue(
                        path="",
                        code="page_in_use",
                        message=_("This page is linked from %(documents)s.")
                        % {"documents": ", ".join(users)},
                    )
                ]
            )
        previous = published_versions()
        document.delete()
        if key in previous:
            _create_release(
                staff_id(user),
                {k: v for k, v in previous.items() if k != key},
                note=_("Page deleted"),
            )
