"""The typed data props against the cases both repositories run.

``tests/fixtures/builder/data-cases.json`` is the storefront's file, copied
and pinned (``.github/scripts/sync_upstream_sources.py``): a declaration, a
value, whether it is valid, the issues, the references and the query
dependencies it yields.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from builder import data_sources

CASES = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "builder"
        / "data-cases.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_the_shared_cases(case):
    outcome = data_sources.check(case["declaration"], case["value"])

    assert outcome.valid is case["valid"]
    assert [{"path": p.path, "code": p.code} for p in outcome.problems] == case[
        "issues"
    ]
    assert sorted(outcome.refs) == sorted(tuple(r) for r in case["refs"])
    assert sorted(outcome.deps) == sorted(tuple(d) for d in case["deps"])


def test_a_value_that_is_not_valid_names_nothing():
    outcome = data_sources.check(
        {
            "field": "query",
            "source": "product",
            "onEmpty": "hide",
            "maxLimit": 6,
        },
        {"mode": "filter", "categories": [1], "ordering": "newest", "limit": 9},
    )

    assert not outcome.valid
    assert outcome.refs == set()
    assert outcome.deps == set()
