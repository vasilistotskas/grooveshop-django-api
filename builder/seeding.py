"""The documents a new store starts with.

``builder/seeds/*.json`` are the storefront's default documents, COPIED from
it (pinned, drift-checked: ``.github/scripts/sync_storefront_sources.py``)
because the storefront owns the blocks and so what a sensible default is.
Each file is ``{key, document, i18n}``: the key it is for, the document and
its translations.

Provisioning calls :func:`seed_store`, which stores each one as a document
(its draft equal to its published content), one version, and ONE release
(number 1) holding them all: what the storefront reads from the first
request. It makes no call to the storefront: a catalogue fetch that fails
must not leave a store without its homepage. What a seed derives (block types,
the dependency rows of its data settings) comes from ``seed_catalogue.json``,
the storefront's catalogue pinned with the seeds, so the seeds and the blocks
they are read against are always of one build.

:func:`seed_with_content` releases richer content in the same files' shape
(the demo store's pages, ``devtools/demo_pages``) on a store that holds
nothing an editor made. That content names the store's own records, so it
is validated against the store's catalogue, references included, like a
publish.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from typing import Any

from django.db import transaction

from builder import storefront, validation
from builder.errors import DocumentInvalid, Issue
from builder.models import (
    BuilderDocument,
    BuilderRelease,
    BuilderScheduledPublish,
    BuilderVersion,
    VersionKind,
    kind_of_key,
)
from builder.services import (
    _catalogue,
    _create_release,
    _issues,
    _lock_release_sequence,
    _new_version,
    published_versions,
)

SEEDS = Path(__file__).parent / "seeds"
SEED_CATALOGUE = Path(__file__).parent / "seed_catalogue.json"

NOTE = "Default content"
#: The release that gives a new store its theme (and an existing one,
#: ``import_theme_settings``): made by the platform, like the defaults.
THEME_NOTE = "Theme"


@dataclass(frozen=True, slots=True)
class Seed:
    key: str
    data: dict[str, Any]
    i18n: dict[str, Any]


def load_seeds(directory: Path = SEEDS) -> list[Seed]:
    """Every seed, in file-name order."""
    seeds = []
    for path in sorted(directory.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        seeds.append(
            Seed(
                key=payload["key"],
                data=payload["document"],
                i18n=payload["i18n"],
            )
        )
    return seeds


@cache
def seed_catalogue() -> storefront.Catalogue:
    """The catalogue of the build the seeds came from."""
    return storefront.parse_catalogue(
        json.loads(SEED_CATALOGUE.read_text(encoding="utf-8"))
    )


def derive(seed: Seed) -> validation.Validated:
    """A seed as validation derives it, against :func:`seed_catalogue`.

    A seed names no record a new store does not have, but its lists are
    data settings, and their dependency rows are what purges the page when
    the store's products change. Every feature counts as on: whether a
    block shows is the storefront's to decide when it renders.
    ``tests/unit/builder/test_seeds.py`` holds every seed to passing.
    """
    issues, validated = validation.validate_document(
        key=seed.key,
        data=seed.data,
        i18n=seed.i18n,
        catalogue=seed_catalogue(),
        feature_on=lambda _feature: True,
    )
    if validated is None:
        raise DocumentInvalid(issues)
    return validated


def seed_store() -> BuilderRelease | None:
    """Seed the store being served; ``None`` when there is nothing to seed
    or the store already has builder data (idempotent: provisioning may be
    re-run, and it must never overwrite what an editor has done)."""
    seeds = load_seeds()
    if not seeds:
        return None
    with transaction.atomic():
        if (
            BuilderDocument.objects.all_with_deleted().exists()
            or BuilderRelease.objects.exists()
        ):
            return None
        versions: dict[str, int] = {}
        for seed in seeds:
            kind = kind_of_key(seed.key)
            assert kind is not None, seed.key
            validated = derive(seed)
            document = BuilderDocument.objects.create(
                kind=kind,
                key=seed.key,
                draft=validated.data,
                draft_i18n=validated.i18n,
                draft_revision=1,
                published_revision=1,
            )
            versions[seed.key] = _new_version(
                None,
                document,
                validated,
                kind=VersionKind.PUBLISH,
                note=NOTE,
                catalogue_hash="",
            ).pk
        return _create_release(None, versions, note=NOTE)


def _untouched_defaults() -> dict[str, int] | None:
    """The release map of a store whose builder data is exactly what
    :func:`seed_store` leaves, ``{}`` for a store with none at all, and
    ``None`` for any other: a draft saved, a page deleted, a named save, a
    schedule or a release by a person all mean someone has worked here."""
    releases = list(BuilderRelease.objects.order_by("number"))
    documents = BuilderDocument.objects.all_with_deleted()
    if not releases:
        return None if documents.exists() else {}
    if (
        len(releases) > 2
        or releases[0].note != NOTE
        or any(
            r.published_by_staff_id is not None
            or r.rolled_back_from_id is not None
            for r in releases
        )
        or any(r.note != THEME_NOTE for r in releases[1:])
        or documents.filter(is_deleted=True).exists()
        or documents.exclude(draft_revision=1).exists()
        or BuilderVersion.objects.exclude(kind=VersionKind.PUBLISH).exists()
        or BuilderVersion.objects.exclude(created_by_staff_id=None).exists()
        or BuilderScheduledPublish.objects.exists()
    ):
        return None
    return dict(releases[-1].versions)


def seed_with_content(seeds: list[Seed], *, note: str) -> BuilderRelease | None:
    """Release *seeds* as a store's content in ONE release, validated like a
    publish; ``None``, writing nothing, unless the store has no builder data
    or only the untouched defaults (:func:`_untouched_defaults`), so it can
    never overwrite what an editor has done.

    Unlike the defaults, content may name the store's records (a product, a
    content page, another page), so it is not trusted: every seed is
    validated against the store's catalogue, its references included, and
    one problem anywhere is a ``DocumentInvalid`` that writes nothing. With
    no catalogue nothing is written either (``StorefrontUnavailableError``),
    the rule of every builder write. A default document no seed names stays
    as it was.
    """
    catalogue = _catalogue()
    with transaction.atomic():
        _lock_release_sequence()
        base = _untouched_defaults()
        if base is None:
            return None
        problems: list[Issue] = []
        checked: dict[str, validation.Validated] = {}
        for seed in seeds:
            issues, validated = _issues(
                seed.key, seed.data, seed.i18n, catalogue
            )
            if validated is not None:
                checked[seed.key] = validated
            problems.extend(
                replace(issue, document=seed.key) for issue in issues
            )
        if problems:
            raise DocumentInvalid(problems)
        documents: dict[str, BuilderDocument] = {}
        for seed in seeds:
            kind = kind_of_key(seed.key)
            assert kind is not None, seed.key
            document, _ = BuilderDocument.objects.get_or_create(
                key=seed.key, defaults={"kind": kind, "draft_revision": 0}
            )
            document.draft = checked[seed.key].data
            document.draft_i18n = checked[seed.key].i18n
            document.draft_catalogue_hash = catalogue.hash
            document.draft_revision += 1
            document.published_revision = document.draft_revision
            document.save()
            documents[seed.key] = document
        # References are checked once every seed is a document: a seed may
        # link another (a menu linking the about page).
        for seed in seeds:
            problems.extend(
                replace(issue, document=seed.key)
                for issue in validation.check_references(checked[seed.key].refs)
            )
        if problems:
            raise DocumentInvalid(problems)
        versions = dict(base)
        for key, document in documents.items():
            versions[key] = _new_version(
                None,
                document,
                checked[key],
                kind=VersionKind.PUBLISH,
                note=note,
                catalogue_hash=catalogue.hash,
            ).pk
        return _create_release(None, versions, note=note)


def installable_problems(key: str, data: Any, i18n: Any) -> list[Issue]:
    """Every problem that would stop :func:`install_document`: the document
    against the store's catalogue view, then what stops a release (references,
    a theme's contrast). Writes nothing."""
    catalogue = _catalogue()
    issues, validated = _issues(key, data, i18n, catalogue)
    if validated is not None:
        issues = validation.check_before_release(key, validated, catalogue)
    return [replace(issue, document=key) for issue in issues]


def install_document(
    key: str, data: Any, i18n: Any, *, note: str
) -> BuilderRelease | None:
    """Create document *key* with *data* and release it in ONE release that
    keeps every other document as it was; ``None``, writing nothing, when the
    store already has that document (it never overwrites what an editor has).

    Validated like a publish, against the store's catalogue view, so one
    problem is a ``DocumentInvalid`` that writes nothing; no catalogue, no
    write. For the content a store starts with that was not a default seed:
    the theme of a store that existed before it had one (``import_theme_settings``).
    """
    catalogue = _catalogue()
    with transaction.atomic():
        _lock_release_sequence()
        if BuilderDocument.objects.all_with_deleted().filter(key=key).exists():
            return None
        issues, validated = _issues(key, data, i18n, catalogue)
        if validated is not None:
            issues = validation.check_before_release(key, validated, catalogue)
        if issues:
            raise DocumentInvalid(
                [replace(issue, document=key) for issue in issues]
            )
        assert validated is not None
        kind = kind_of_key(key)
        assert kind is not None, key
        document = BuilderDocument.objects.create(
            kind=kind,
            key=key,
            draft=validated.data,
            draft_i18n=validated.i18n,
            draft_catalogue_hash=catalogue.hash,
            draft_revision=1,
            published_revision=1,
        )
        version = _new_version(
            None,
            document,
            validated,
            kind=VersionKind.PUBLISH,
            note=note,
            catalogue_hash=catalogue.hash,
        )
        versions = {**published_versions(), key: version.pk}
        return _create_release(None, versions, note=note)


def install_theme_defaults() -> BuilderRelease | None:
    """The theme a NEW store of this catalogue view's design starts with
    (``documentKinds.theme.defaults``), installed as its theme document.
    ``None`` when the catalogue states no defaults or the store already has
    a theme document."""
    root = _catalogue().roots.get("theme")
    if root is None or root.defaults is None:
        return None
    return install_document(
        "theme",
        {"sections": {}, "order": [], "settings": root.defaults},
        {},
        note=THEME_NOTE,
    )
