"""What a button's action may hold, against the catalogue's own rule.

``tests/fixtures/builder/action-cases.json`` is the storefront's list, a pinned
copy its own spec proves against the block as it is declared. The storefront
writes the rule twice, as ``.refine`` and as ``oneOf``, and Django holds only
the second, so this is what keeps a save here and a parse there from disagreeing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from builder.seeding import SEED_CATALOGUE
from builder.storefront import parse_catalogue

CASES = json.loads(
    (
        Path(__file__).parents[2] / "fixtures/builder/action-cases.json"
    ).read_text(encoding="utf-8")
)
CATALOGUE = parse_catalogue(
    json.loads(SEED_CATALOGUE.read_text(encoding="utf-8"))
)
HERO_BANNER = next(b for b in CATALOGUE.blocks if b.type == "hero_banner")


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_the_catalogue_holds_an_action_to_the_shared_cases(case):
    assert HERO_BANNER.validator.is_valid(case["settings"]) is case["valid"]


def test_every_block_with_an_action_tells_django_what_it_names():
    for name, setting in (
        ("hero_banner", "ctaAction"),
        ("media_text", "ctaAction"),
        ("cta_banner", "buttonAction"),
    ):
        block = next(b for b in CATALOGUE.blocks if b.type == name)

        assert f"/{setting}/to" in block.link_refs
        assert block.data[f"/{setting}/product"]["kind"] == "product"
        assert block.data[f"/{setting}/promotion"]["kind"] == "promotion"
