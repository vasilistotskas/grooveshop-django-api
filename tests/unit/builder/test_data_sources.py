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

#: What the storefront's catalogue states (``dataLimits``): the
#: shared cases are written against these.
LIMITS = data_sources.DataLimits(
    max_picked_ids=24,
    max_filter_ids=5,
    max_queries_per_document=12,
    max_query_limit=24,
)

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
    outcome = data_sources.check(case["declaration"], case["value"], LIMITS)

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
        LIMITS,
    )

    assert not outcome.valid
    assert outcome.refs == set()
    assert outcome.deps == set()


class TestTheCataloguesLimits:
    def test_a_catalogue_that_declares_data_must_state_its_limits(self):
        from builder.storefront import StorefrontUnavailable, parse_catalogue
        from tests.utils.builder import FakeCatalogue

        fake = FakeCatalogue()
        hero = next(b for b in fake.blocks if b["type"] == "hero")
        hero["data"] = {
            "/productId": {
                "field": "ref",
                "kind": "product",
                "onMissing": "hide",
            }
        }
        payload = {
            "hash": fake.hash,
            "blocks": fake.blocks,
            "documentKinds": fake.document_kinds,
            "templates": ["home"],
        }

        with pytest.raises(StorefrontUnavailable):
            parse_catalogue(payload)

        payload["dataLimits"] = {
            "maxPickedIds": 24,
            "maxFilterIds": 5,
            "maxQueriesPerDocument": 12,
            "maxQueryLimit": 24,
        }
        assert parse_catalogue(payload).data_limits == LIMITS

    @pytest.mark.parametrize("bad", [0, -1, True, "5", None])
    def test_a_limit_is_a_whole_number_of_one_or_more(self, bad):
        with pytest.raises((ValueError, TypeError, KeyError)):
            data_sources.DataLimits.parse(
                {
                    "maxPickedIds": bad,
                    "maxFilterIds": 5,
                    "maxQueriesPerDocument": 12,
                    "maxQueryLimit": 24,
                }
            )


class TestTheProductStockKey:
    DECLARATION = {
        "field": "query",
        "source": "product",
        "onEmpty": "hide",
        "maxLimit": 6,
    }

    def _check(self, **extra):
        value = {"mode": "filter", "ordering": "newest", "limit": 6, **extra}
        return data_sources.check(self.DECLARATION, value, LIMITS)

    @pytest.mark.parametrize("stock", data_sources.PRODUCT_STOCK)
    def test_each_stock_value_is_accepted(self, stock):
        outcome = self._check(stock=stock)

        assert outcome.valid
        assert outcome.deps == {("queryProduct", "*")}

    def test_no_stock_key_is_accepted(self):
        assert self._check().valid

    def test_stock_does_not_change_the_facets(self):
        outcome = self._check(categories=[3], stock="inStockOnly")

        assert outcome.deps == {("queryProduct", "category:3")}

    @pytest.mark.parametrize("bad", ["soldOut", "", "INSTOCKONLY", None])
    def test_another_value_is_refused(self, bad):
        outcome = self._check(stock=bad)

        assert [(p.path, p.code) for p in outcome.problems] == [
            ("/stock", data_sources.INVALID)
        ]
        assert outcome.deps == set()

    @pytest.mark.parametrize("bad", [True, 1, ["inStockOnly"], {"a": 1}])
    def test_a_value_that_is_not_a_string_is_refused(self, bad):
        outcome = self._check(stock=bad)

        assert [(p.path, p.code) for p in outcome.problems] == [
            ("/stock", data_sources.INVALID)
        ]

    def test_a_picked_list_takes_no_stock(self):
        outcome = data_sources.check(
            self.DECLARATION,
            {"mode": "picked", "ids": [1], "stock": "inStockOnly"},
            LIMITS,
        )

        assert [(p.path, p.code) for p in outcome.problems] == [
            ("/stock", data_sources.UNKNOWN_FILTER)
        ]

    def test_a_blog_post_list_takes_no_stock(self):
        outcome = data_sources.check(
            {**self.DECLARATION, "source": "blogPost"},
            {
                "mode": "filter",
                "ordering": "newest",
                "limit": 6,
                "stock": "any",
            },
            LIMITS,
        )

        assert [(p.path, p.code) for p in outcome.problems] == [
            ("/stock", data_sources.UNKNOWN_FILTER)
        ]
