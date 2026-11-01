"""The documents a new store starts with.

``builder/seeds/*.json`` are the storefront's default documents, COPIED from
it (pinned, drift-checked: ``.github/scripts/sync_storefront_sources.py``)
because the storefront owns the blocks and so what a sensible default is.
Each file is a converter fixture: the document is ``response.document``, its
translations ``response.i18n``, and the key it is for ``request.key``.

Provisioning calls :func:`seed_store`, which stores each one as a document
(its draft equal to its published content), one version, and ONE release
(number 1) holding them all: what the storefront reads from the first
request. It makes no call to the storefront and so does not validate: the
catalogue is not needed to start a store, and a catalogue fetch that fails
must not leave a store without its homepage. What stands in for validation
is ``tests/unit/builder/test_seeds.py``, which validates every seed against
the vendored catalogue snapshot and checks that what is derived here (block
types, references) is what validation derives.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.db import transaction

from builder import validation
from builder.models import (
    BuilderDocument,
    BuilderRelease,
    VersionKind,
    kind_of_key,
)
from builder.services import _create_release, _new_version

SEEDS = Path(__file__).parent / "seeds"

NOTE = "Default content"


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
                key=payload["request"]["key"],
                data=payload["response"]["document"],
                i18n=payload["response"]["i18n"],
            )
        )
    return seeds


def _block_types(nodes: dict[str, Any]) -> set[str]:
    types: set[str] = set()
    for node in nodes.values():
        types.add(node["type"])
        types |= _block_types(node.get("blocks", {}))
    return types


def derive(seed: Seed) -> validation.Validated:
    """What validation would derive from a seed, without the catalogue.

    A seed holds no references (a default document cannot name a product
    or a page a new store does not have), so ``refs`` is empty; the seeds
    test holds every seed to that.
    """
    payload = validation.encode(seed.data, seed.i18n)
    return validation.Validated(
        data=seed.data,
        i18n=seed.i18n,
        block_types=sorted(_block_types(seed.data["sections"])),
        refs={},
        content_hash=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
    )


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
