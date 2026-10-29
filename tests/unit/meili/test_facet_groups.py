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
    client.tenant_search_client.return_value.multi_search.return_value = {
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
                selected=frozenset({"10", "11"}),
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
        engine.tenant_search_client.return_value.multi_search.assert_not_called()

    def test_no_groups_is_a_plain_search(self, engine):
        IndexQuerySet(FacetModel).set_facets(FACET).search("q")

        engine.get_search_index.return_value.search.assert_called_once()
        engine.tenant_search_client.return_value.multi_search.assert_not_called()

    def test_one_count_query_per_group_leaves_out_its_own_clause(self, engine):
        _multi_search(engine, {}, {}, {})

        self._queryset().search("blue")

        (queries,) = (
            engine.tenant_search_client.return_value.multi_search.call_args.args
        )
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

        (queries,) = (
            engine.tenant_search_client.return_value.multi_search.call_args.args
        )
        for query in queries:
            assert query["matchingStrategy"] == "all"
            assert query["locales"] == ["el"]

    def test_siblings_are_counted_from_their_group_query(self, engine):
        # The first group selects 10 and 11 and also governs 12; the
        # second selects 20 and also governs 21. 30 belongs to no group.
        _multi_search(
            engine,
            {
                "facetDistribution": {
                    # 12 is narrowed here: only documents holding a selected
                    # value of the group are counted
                    FACET: {"10": 1, "12": 1, "20": 1, "30": 4},
                    "category": {"7": 2},
                }
            },
            # without the first group's clause; 99 is not a member
            {"facetDistribution": {FACET: {"10": 1, "12": 5, "99": 8}}},
            # without the second group's clause
            {"facetDistribution": {FACET: {"20": 1, "21": 2}}},
        )

        results = self._queryset().search("q")

        assert results["facetDistribution"] == {
            FACET: {"10": 1, "12": 5, "20": 1, "21": 2, "30": 4},
            "category": {"7": 2},
        }

    def test_a_selected_value_keeps_its_main_count_when_the_count_query_omits_it(
        self, engine
    ):
        # The count query matches more documents than the main one, so the
        # engine's facet value cap can push a selected value out of it.
        _multi_search(
            engine,
            {"facetDistribution": {FACET: {"10": 1, "20": 1}}},
            {"facetDistribution": {FACET: {"12": 4}}},
            {"facetDistribution": {FACET: {"21": 2}}},
        )

        results = self._queryset().search("q")

        assert results["facetDistribution"] == {
            FACET: {"10": 1, "12": 4, "20": 1, "21": 2}
        }

    def test_a_sibling_the_count_query_omits_is_not_listed(self, engine):
        _multi_search(
            engine,
            {"facetDistribution": {FACET: {"10": 1, "12": 1, "20": 1}}},
            {"facetDistribution": {FACET: {}}},
            {"facetDistribution": {FACET: {"21": 2}}},
        )

        results = self._queryset().search("q")

        # 12 is the first group's sibling and its own query lacks it
        assert results["facetDistribution"] == {
            FACET: {"10": 1, "20": 1, "21": 2}
        }

    def test_a_main_result_with_no_distribution_still_gets_the_siblings(
        self, engine
    ):
        _multi_search(
            engine,
            {},
            {"facetDistribution": {FACET: {"10": 2, "12": 3}}},
            {"facetDistribution": {FACET: {"21": 1}}},
        )

        results = self._queryset().search("q")

        # 10 is selected, so it is the main query's to count, not this one's
        assert results["facetDistribution"] == {FACET: {"12": 3, "21": 1}}

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


class TestWholeFieldGroup:
    """A group with ``members=None`` is the whole facet field, as the
    category and brand filters are."""

    def _queryset(self):
        return (
            IndexQuerySet(FacetModel)
            .filter(active=True)
            .filter_facet_group("brand", [1, 2], members=None)
            .filter(in_stock=True)
            .set_facets("brand")
        )

    def test_the_group_records_no_members(self):
        (group,) = self._queryset()._state.facet_groups

        assert group.members is None
        assert group.clause == "brand IN [1, 2]"
        assert group.selected == frozenset({"1", "2"})

    def test_the_count_query_leaves_out_the_clause_and_keeps_the_rest(
        self, engine
    ):
        _multi_search(engine, {}, {})

        self._queryset().search("q")

        (queries,) = (
            engine.tenant_search_client.return_value.multi_search.call_args.args
        )
        _, counts = queries
        assert counts["filter"] == ["active = True", "in_stock = True"]
        assert (counts["facets"], counts["limit"]) == (["brand"], 0)

    def test_every_key_of_the_count_query_is_listed_selected_or_not(
        self, engine
    ):
        _multi_search(
            engine,
            {"facetDistribution": {"brand": {"1": 1}}},
            {"facetDistribution": {"brand": {"1": 1, "2": 4, "3": 6}}},
        )

        results = self._queryset().search("q")

        assert results["facetDistribution"] == {
            "brand": {"1": 1, "2": 4, "3": 6}
        }

    def test_a_count_query_with_no_distribution_lists_nothing(self, engine):
        _multi_search(engine, {"facetDistribution": {"brand": {"1": 1}}}, {})

        results = self._queryset().search("q")

        assert results["facetDistribution"] == {"brand": {}}


class TestStatsBaselines:
    def _queryset(self):
        everything = IndexQuerySet(FacetModel).filter(active=True)
        return (
            IndexQuerySet(FacetModel)
            .filter(active=True, brand__in=[1])
            .set_facets("category")
            .stats_over(everything.locales("el"), "price")
            .stats_over(IndexQuerySet(FacetModel), "views", "likes")
        )

    def test_a_baseline_alone_makes_the_search_a_multi_search(self, engine):
        _multi_search(engine, {}, {}, {})

        self._queryset().search("blue")

        engine.get_search_index.return_value.search.assert_not_called()
        (queries,) = (
            engine.tenant_search_client.return_value.multi_search.call_args.args
        )
        assert len(queries) == 3

    def test_a_baseline_query_has_no_text_and_only_its_own_filters(
        self, engine
    ):
        _multi_search(engine, {}, {}, {})

        self._queryset().search("blue")

        (queries,) = (
            engine.tenant_search_client.return_value.multi_search.call_args.args
        )
        main, price, popularity = queries
        assert main["q"] == "blue"
        assert main["filter"] == ["active = True", "brand IN [1]"]
        assert main["facets"] == ["category"]
        assert price["q"] == ""
        assert price["filter"] == ["active = True"]
        assert price["facets"] == ["price"]
        assert price["locales"] == ["el"]
        assert (price["offset"], price["limit"]) == (0, 0)
        assert popularity["q"] == ""
        assert popularity["filter"] == []
        assert popularity["facets"] == ["views", "likes"]
        assert popularity["locales"] == []

    def test_the_stats_are_taken_from_the_baselines_not_the_search(
        self, engine
    ):
        _multi_search(
            engine,
            {
                "facetDistribution": {"category": {"7": 1}},
                "facetStats": {"price": {"min": 5.0, "max": 6.0}},
            },
            {"facetStats": {"price": {"min": 1.0, "max": 9.0}}},
            {"facetStats": {"views": {"min": 0, "max": 3}}},
        )

        results = self._queryset().search("blue")

        assert results["facetStats"] == {
            "price": {"min": 1.0, "max": 9.0},
            "views": {"min": 0, "max": 3},
        }
        assert results["facetDistribution"] == {"category": {"7": 1}}

    def test_an_empty_baseline_adds_no_stats(self, engine):
        _multi_search(engine, {}, {}, {})

        results = self._queryset().search("blue")

        assert results.get("facetStats", {}) == {}

    def test_baseline_time_counts_toward_the_engine_time(self, engine):
        _multi_search(
            engine,
            {"processingTimeMs": 1},
            {"processingTimeMs": 2},
            {"processingTimeMs": 3},
        )

        assert self._queryset().search("q")["processing_time_ms"] == 6
