"""The storefront's real block catalogue, as each design is served it.

``builder/catalogues/<design>.json`` is the pinned copy of what ``GET
/api/builder/catalogue?schema=<design>`` answers (``.github/scripts/
sync_upstream_sources.py``): one per tenant schema the storefront names a
design for, and ``default`` for every store whose schema no design names.
A store reads only its design's, so a test that holds stored content to the
real blocks runs against the design whose stores hold it.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any

from builder.seeding import CATALOGUES
from builder.storefront import BlockDefinition, Catalogue, parse_catalogue

#: Every design with a catalogue of its own, ``default`` included.
DESIGNS: tuple[str, ...] = tuple(
    sorted(path.stem for path in CATALOGUES.glob("*.json"))
)


@cache
def payload(design: str) -> dict[str, Any]:
    """The catalogue *design* is served, as the storefront sends it."""
    return json.loads(
        (CATALOGUES / f"{design}.json").read_text(encoding="utf-8")
    )


@cache
def catalogue(design: str) -> Catalogue:
    """The catalogue *design* is served, as Django parses it."""
    return parse_catalogue(payload(design))


def block(type_name: str) -> BlockDefinition | None:
    """The definition of *type_name* in whichever design draws it: one build
    states every block once, so each design that lists it lists the same."""
    return next(
        (
            definition
            for design in DESIGNS
            if (definition := catalogue(design).get(type_name)) is not None
        ),
        None,
    )
