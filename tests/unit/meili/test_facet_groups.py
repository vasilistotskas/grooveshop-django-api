"""IndexQuerySet facet groups: the filter strings and the count queries.

A facet group is one multi-select group inside a facet field (the product
index's ``attribute_values`` lists the values of every attribute). The
group's own values are counted without its own selection. Everything here
runs against a mocked client; the behaviour against a real engine is in
``tests/integration/search/test_product_search_attribute_filters.py``.
"""

from unittest.mock import MagicMock, patch

import pytest

from meili.querysets import FacetGroup, IndexQuerySet


class FacetModel:
    __name__ = "FacetModel"
    _meilisearch = {"base_index_name": "facet_index", "supports_geo": False}

    class MeiliMeta:
        primary_key = "id"

    @classmethod
    def get_meili_index_name(cls):
        return "facet_index"


FACET = "attribute_values"


@pytest.fixture
def engine():
    """The patched client, a no-hit single search and a multi-search."""
    with patch("meili.querysets.client") as client:
        client.get_index.return_value = MagicMock()
        client.get_search_index.return_value.search.return_value = {
            "hits": [],
            "estimatedTotalHits": 0,
        }
        yield client


def _multi_search(client, *results):
    client.search_client.multi_search.return_value = {
        "results": [{"hits": [], **result} for result in results]
    }


class TestFilterFacetGroup:
    def test_builds_an_in_clause_and_records_the_group(self):
        queryset = IndexQuerySet(FacetModel).filter_facet_group(
            FACET, [10, 11], members=[10, 11, 12]
        )

        assert queryset.filters == ["attribute_values IN [10, 11]"]
        assert queryset._state.facet_groups == [
            FacetGroup(
                facet=FACET,
                clause="attribute_values IN [10, 11]",
                members=frozenset({"10", "11", "12"}),
            )
        ]

    def test_groups_and_other_filters_are_anded(self):
        queryset = (
            IndexQuerySet(FacetModel)
            .filter(active=True)
            .filter_facet_group(FACET, [10, 11], members=[10, 11])
            .filter_facet_group(FACET, [20], members=[20, 21])
        )

        # A Meilisearch filter list ANDs its elements.
        assert queryset.filters == [
            "active = True",
            "attribute_values IN [10, 11]",
            "attribute_values IN [20]",
        ]

    def test_returns_the_queryset_for_chaining(self):
        queryset = IndexQuerySet(FacetModel)

        assert queryset.filter_facet_group(FACET, [1], members=[1]) is queryset


class TestSearchWithGroupCounts:
    def _queryset(self, *, facets=(FACET,)):
        return (
            IndexQuerySet(FacetModel)
            .filter(active=True)
            .filter_facet_group(FACET, [10, 11], members=[10, 11, 12])
            .filter_facet_group(FACET, [20], members=[20, 21])
            .set_facets(*facets)
        )

    def test_a_group_whose_facet_is_not_requested_is_a_plain_search(
        self, engine
    ):
        self._queryset(facets=("category",)).search("q")

        engine.get_search_index.return_value.search.assert_called_once()
        engine.search_client.multi_search.assert_not_called()

    def test_no_groups_is_a_plain_search(self, engine):
        IndexQuerySet(FacetModel).set_facets(FACET).search("q")

        engine.get_search_index.return_value.search.assert_called_once()
        engine.search_client.multi_search.assert_not_called()

    def test_one_count_query_per_group_leaves_out_its_own_clause(self, engine):
        _multi_search(engine, {}, {}, {})

        self._queryset().search("blue")

        (queries,) = engine.search_client.multi_search.call_args.args
        main, first, second = queries
        assert main["indexUid"] == "facet_index"
        assert main["q"] == "blue"
        assert main["filter"] == [
            "active = True",
            "attribute_values IN [10, 11]",
            "attribute_values IN [20]",
        ]
        assert main["facets"] == [FACET]
        assert main["limit"] == 20

        assert first["q"] == second["q"] == "blue"
        assert first["filter"] == [
            "active = True",
            "attribute_values IN [20]",
        ]
        assert second["filter"] == [
            "active = True",
            "attribute_values IN [10, 11]",
        ]
        for count_query in (first, second):
            assert count_query["facets"] == [FACET]
            assert (count_query["limit"], count_query["offset"]) == (0, 0)

    def test_count_queries_match_the_same_documents_as_the_search(self, engine):
        _multi_search(engine, {}, {}, {})

        self._queryset().matching_strategy("all").locales("el").search("q")

        (queries,) = engine.search_client.multi_search.call_args.args
        for query in queries:
            assert query["matchingStrategy"] == "all"
            assert query["locales"] == ["el"]

    def test_members_are_counted_from_their_own_group_query(self, engine):
        _multi_search(
            engine,
            {
                "facetDistribution": {
                    FACET: {"10": 1, "20": 1, "30": 4},
                    "category": {"7": 2},
                }
            },
            # without the first group's clause: 10 and 11 and 12 compete
            {"facetDistribution": {FACET: {"10": 5, "11": 3, "99": 8}}},
            # without the second group's clause
            {"facetDistribution": {FACET: {"20": 6, "21": 2}}},
        )

        results = self._queryset().search("q")

        assert results["facetDistribution"] == {
            # 30 belongs to no group: it keeps the main query's count;
            # 99 is not a member of the first group: ignored
            FACET: {"30": 4, "10": 5, "11": 3, "20": 6, "21": 2},
            "category": {"7": 2},
        }

    def test_a_member_the_group_query_does_not_return_is_dropped(self, engine):
        _multi_search(
            engine,
            {"facetDistribution": {FACET: {"10": 1}}},
            {"facetDistribution": {FACET: {}}},
            {"facetDistribution": {FACET: {"20": 1}}},
        )

        results = self._queryset().search("q")

        # 10 is counted by the first group's own query, which has no 10
        assert results["facetDistribution"] == {FACET: {"20": 1}}

    def test_a_main_result_with_no_distribution_still_gets_the_counts(
        self, engine
    ):
        _multi_search(
            engine,
            {},
            {"facetDistribution": {FACET: {"10": 2}}},
            {"facetDistribution": {FACET: {"20": 1}}},
        )

        results = self._queryset().search("q")

        assert results["facetDistribution"] == {FACET: {"10": 2, "20": 1}}

    def test_engine_time_is_the_sum_of_every_query(self, engine):
        _multi_search(
            engine,
            {"processingTimeMs": 3, "estimatedTotalHits": 9},
            {"processingTimeMs": 4},
            {"processingTimeMs": 5},
        )

        results = self._queryset().search("q")

        assert results["processing_time_ms"] == 12
        assert results["estimated_total_hits"] == 9
