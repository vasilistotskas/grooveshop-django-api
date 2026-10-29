"""The granular ``filterableAttributes`` payload and the index settings.

Meilisearch indexes every filterable attribute for every operator unless
told otherwise, so the settings say which fields compare, which only
match, and whether the facet-search database is built
(https://www.meilisearch.com/docs/learn/filtering_and_sorting/configure_granular_filters).
"""

from unittest.mock import MagicMock, patch

import pytest

from blog.models.post import BlogPostTranslation
from meili._client import Client
from meili._settings import _MeiliSettings
from meili.dataclasses import MeiliIndexSettings
from product.factories.product import ProductFactory
from product.models.product import ProductTranslation


def _entry(patterns, comparison, facet_search=False):
    return {
        "attributePatterns": patterns,
        "features": {
            "facetSearch": facet_search,
            "filter": {"equality": True, "comparison": comparison},
        },
    }


class TestFilterableAttributesPayload:
    def test_range_fields_compare_and_the_rest_only_match(self):
        settings = MeiliIndexSettings(
            filterable_fields=["brand", "price", "active", "views"],
            range_fields=["price", "views"],
        )

        assert settings.filterable_attributes_payload() == [
            _entry(["price", "views"], comparison=True),
            _entry(["brand", "active"], comparison=False),
        ]

    def test_no_range_fields_is_one_equality_entry(self):
        settings = MeiliIndexSettings(filterable_fields=["a", "b"])

        assert settings.filterable_attributes_payload() == [
            _entry(["a", "b"], comparison=False)
        ]

    def test_only_range_fields_is_one_comparison_entry(self):
        settings = MeiliIndexSettings(
            filterable_fields=["a"], range_fields=["a"]
        )

        assert settings.filterable_attributes_payload() == [
            _entry(["a"], comparison=True)
        ]

    def test_nothing_filterable_is_empty(self):
        assert MeiliIndexSettings().filterable_attributes_payload() == []

    def test_facet_search_reaches_every_entry(self):
        settings = MeiliIndexSettings(
            filterable_fields=["a", "b"],
            range_fields=["a"],
            facet_search=True,
        )

        assert [
            entry["features"]["facetSearch"]
            for entry in settings.filterable_attributes_payload()
        ] == [True, True]

    def test_facet_search_is_off_by_default(self):
        assert MeiliIndexSettings().facet_search is False

    def test_a_range_field_must_be_filterable(self):
        settings = MeiliIndexSettings(
            filterable_fields=["a"], range_fields=["a", "ghost"]
        )

        with pytest.raises(ValueError, match="ghost"):
            settings.filterable_attributes_payload()


class TestWithSettingsPayload:
    @patch("meili._client._Client")
    def test_sends_the_granular_entries_and_the_index_wide_flag(
        self, mock_client_class
    ):
        mock_index = MagicMock()
        mock_client_class.return_value.index.return_value = mock_index
        client = Client(
            _MeiliSettings(
                host="localhost",
                port=7700,
                https=False,
                master_key="k",
                search_key="k",
                timeout=10,
                sync=False,
            )
        )
        index_settings = MeiliIndexSettings(
            filterable_fields=["a", "b"], range_fields=["a"]
        )

        client.with_settings("idx", index_settings)

        body = mock_index.update_settings.call_args[0][0]
        assert body["filterableAttributes"] == (
            index_settings.filterable_attributes_payload()
        )
        assert body["facetSearch"] is False


class TestModelSettings:
    def test_product_filters(self):
        settings = ProductTranslation.get_meili_settings()

        assert settings.filterable_attributes_payload() == [
            _entry(["effective_price", "likes_count", "view_count"], True),
            _entry(
                [
                    "language_code",
                    "category",
                    "brand",
                    "attribute_values",
                    "active",
                    "is_deleted",
                    "in_stock",
                    "on_offer",
                ],
                False,
            ),
        ]
        assert settings.facet_search is False
        assert settings.sortable_fields == [
            "likes_count",
            "effective_price",
            "view_count",
            "created_at",
        ]
        assert settings.faceting == {"maxValuesPerFacet": 500}
        assert settings.pagination == {"maxTotalHits": 20_000}
        assert settings.search_cutoff_ms == 1500

    def test_blog_filters(self):
        settings = BlogPostTranslation.get_meili_settings()

        assert settings.filterable_attributes_payload() == [
            _entry(["language_code", "is_published"], False)
        ]
        assert settings.facet_search is False
        assert settings.pagination == {"maxTotalHits": 20_000}

    @pytest.mark.django_db
    def test_every_filterable_and_sortable_field_is_in_the_document(self):
        """An attribute the engine is told to filter or sort by, but the
        document does not carry, filters nothing: narrowing the lists
        must not drop a field from the serialised document."""
        product = ProductFactory(num_images=0, num_reviews=0)
        translation = ProductTranslation.get_meilisearch_queryset().get(
            pk=product.translations.first().pk
        )
        settings = ProductTranslation.get_meili_settings()

        document = translation.meili_serialize()

        assert set(settings.filterable_fields) <= document.keys()
        assert set(settings.sortable_fields) <= document.keys()


class TestSettingsTaskTimeout:
    def test_settings_tasks_wait_for_the_configured_time(self, settings):
        settings.MEILISEARCH = {
            **settings.MEILISEARCH,
            "SETTINGS_TASK_TIMEOUT_MS": 123_456,
        }
        task = MagicMock(task_uid=7)

        with patch("meili.models._client") as mock_client:
            mock_client.tasks = [task]
            mock_client.wait_for_task.return_value = MagicMock(
                status="succeeded"
            )

            ProductTranslation.update_meili_settings()

        mock_client.wait_for_task.assert_called_once_with(
            7, timeout_in_ms=123_456
        )
