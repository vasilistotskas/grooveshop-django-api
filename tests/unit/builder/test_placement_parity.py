"""Where a section may sit is decided twice, and both must agree.

The editor filters its add-section library with ``builderPlacementAllowed``
(``shared/builder/placement.ts`` in the storefront) so it never offers a
block the store would refuse; this module's ``_placement_allowed`` is the one
that refuses. ``placement-cases.json`` is the storefront's list of cases, a
pinned copy (``.github/scripts/sync_storefront_sources.py``) that its own test
runs ``builderPlacementAllowed`` over. Running ``_placement_allowed`` over the
same cases means a change to the rule on one side that the other does not make
fails one of the two suites.

A case is ``{groups, templates, document, allowed}``: the block's ``groups``
and ``templates`` (``null``: not declared), the document key it is placed at
the top of, and whether that is allowed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from builder.models import kind_of_key
from builder.storefront import _parse_block
from builder.validation import _placement_allowed

CASES: list[dict[str, Any]] = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "builder"
        / "placement-cases.json"
    ).read_text(encoding="utf-8")
)


def block(groups: list[str] | None, templates: list[str] | None):
    """A section definition that declares only where it may sit."""
    raw: dict[str, Any] = {
        "type": "probe",
        "version": 1,
        "schema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
        "linkRefs": [],
        "kinds": ["section"],
    }
    if groups is not None:
        raw["groups"] = groups
    if templates is not None:
        raw["templates"] = templates
    return _parse_block(raw)


def test_the_cases_cover_the_rule():
    assert len(CASES) >= 10
    assert {case["allowed"] for case in CASES} == {True, False}
    assert {kind_of_key(case["document"]) for case in CASES} >= {
        kind
        for kind in (kind_of_key(k) for k in ("theme", "group:a", "page:a"))
    }


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=lambda case: (
        f"{case['document']} groups={case['groups']} "
        f"templates={case['templates']}"
    ),
)
def test_placement_agrees_with_the_editors_filter(case):
    document = case["document"]
    kind = kind_of_key(document)
    assert kind is not None, f"{document} is not a document key"

    allowed = _placement_allowed(
        block(case["groups"], case["templates"]), kind, document
    )

    assert allowed is case["allowed"]
