from unittest.mock import MagicMock, patch

import pytest

from meili.querysets import BoundingBox, IndexQuerySet, Point, Radius


class MockModel:
    __name__ = "MockModel"
    _meilisearch = {"index_name": "test_index", "supports_geo": False}

    class MeiliMeta:
        primary_key = "id"

    objects = MagicMock()


class MockGeoModel:
    __name__ = "MockGeoModel"
    _meilisearch = {"index_name": "test_geo_index", "supports_geo": True}

    class MeiliMeta:
        primary_key = "id"

    objects = MagicMock()


class TestIndexQuerySet:
    def setup_method(self):
        self.mock_index = MagicMock()
        self.mock_client = MagicMock()
        self.mock_client.get_index.return_value = self.mock_index

    @patch("meili.querysets.client")
    def test_queryset_initialization(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)

        assert queryset.model == MockModel
        assert queryset.index == self.mock_index
        mock_client.get_index.assert_called_once_with("test_index")

        assert queryset._IndexQuerySet__offset == 0
        assert queryset._IndexQuerySet__limit == 20
        assert queryset._IndexQuerySet__filters == []
        assert queryset._IndexQuerySet__facets == []
        assert queryset._IndexQuerySet__attributes_to_retrieve == ["*"]
        assert queryset._IndexQuerySet__attributes_to_crop == []
        assert queryset._IndexQuerySet__crop_length == 10
        assert queryset._IndexQuerySet__crop_marker == "..."
        assert queryset._IndexQuerySet__attributes_to_highlight == ["*"]
        assert queryset._IndexQuerySet__highlight_pre_tag == "<mark>"
        assert queryset._IndexQuerySet__highlight_post_tag == "</mark>"
        assert not queryset._IndexQuerySet__show_matches_position
        assert queryset._IndexQuerySet__sort == []
        assert queryset._IndexQuerySet__matching_strategy == "last"
        assert not queryset._IndexQuerySet__show_ranking_score
        assert queryset._IndexQuerySet__attributes_to_search_on == ["*"]
        assert queryset._IndexQuerySet__locales == []

    @patch("meili.querysets.client")
    def test_queryset_repr_and_str(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)

        assert repr(queryset) == "<IndexQuerySet for MockModel>"
        assert str(queryset) == "IndexQuerySet for MockModel"

    @patch("meili.querysets.client")
    def test_queryset_getitem_slice(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset[10:50]

        assert result == queryset
        assert queryset._IndexQuerySet__offset == 10
        assert queryset._IndexQuerySet__limit == 50

    @patch("meili.querysets.client")
    def test_queryset_getitem_invalid_index(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)

        with pytest.raises(
            TypeError, match="IndexQuerySet indices must be slices"
        ):
            queryset[5]

    @patch("meili.querysets.client")
    def test_count(self, mock_client):
        mock_client.get_index.return_value = self.mock_index
        mock_stats = MagicMock()
        mock_stats.number_of_documents = 42
        self.mock_index.get_stats.return_value = mock_stats

        queryset = IndexQuerySet(MockModel)
        count = queryset.count()

        assert count == 42
        self.mock_index.get_stats.assert_called_once()

    @patch("meili.querysets.client")
    def test_paginate(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.paginate(limit=100, offset=25)

        assert result == queryset
        assert queryset._IndexQuerySet__limit == 100
        assert queryset._IndexQuerySet__offset == 25

    @patch("meili.querysets.client")
    def test_order_by_ascending(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.order_by("title", "created_at")

        assert result == queryset
        assert queryset._IndexQuerySet__sort == ["title:asc", "created_at:asc"]

    @patch("meili.querysets.client")
    def test_order_by_descending(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.order_by("-title", "-created_at")

        assert result == queryset
        assert queryset._IndexQuerySet__sort == [
            "title:desc",
            "created_at:desc",
        ]

    @patch("meili.querysets.client")
    def test_order_by_mixed(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.order_by("title", "-created_at", "priority")

        assert result == queryset
        assert queryset._IndexQuerySet__sort == [
            "title:asc",
            "created_at:desc",
            "priority:asc",
        ]

    @patch("meili.querysets.client")
    def test_order_by_geopoint(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.order_by("geoPoint", "-geoPoint")

        assert result == queryset
        assert queryset._IndexQuerySet__sort == [
            "_geoPoint:asc",
            "_geoPoint:desc",
        ]

    @patch("meili.querysets.client")
    def test_matching_strategy(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.matching_strategy("all")

        assert result == queryset
        assert queryset._IndexQuerySet__matching_strategy == "all"

    @patch("meili.querysets.client")
    def test_attributes_to_search_on(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)

        with pytest.raises(
            TypeError, match="list.append\\(\\) takes exactly one argument"
        ):
            queryset.attributes_to_search_on("title", "content")

        result = queryset.attributes_to_search_on("title")
        assert result == queryset

    @patch("meili.querysets.client")
    def test_filter_regular_exact_string(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(title="Test Title")

        assert result == queryset
        assert "title = 'Test Title'" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_regular_exact_number(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(price=100)

        assert result == queryset
        assert "price = 100" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_regular_empty_values(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        queryset.filter(empty_string="", empty_list=[], empty_dict={})

        filters = queryset._IndexQuerySet__filters
        assert "empty_string IS EMPTY" in filters
        assert "empty_list IS EMPTY" in filters
        assert "empty_dict IS EMPTY" in filters

    @patch("meili.querysets.client")
    def test_filter_regular_null_value(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        queryset.filter(nullable_field=None)

        assert "nullable_field IS NULL" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_gte(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(price__gte=100)

        assert result == queryset
        assert "price >= 100" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_gte_invalid_type(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)

        with pytest.raises(
            TypeError, match="Cannot compare <class 'str'> with int or float"
        ):
            queryset.filter(price__gte="invalid")

    @patch("meili.querysets.client")
    def test_filter_gt(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(price__gt=100.5)

        assert result == queryset
        assert "price > 100.5" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_lte(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(price__lte=200)

        assert result == queryset
        assert "price <= 200" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_lt(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(price__lt=50.25)

        assert result == queryset
        assert "price < 50.25" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_in(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(category__in=["electronics", "books"])

        assert result == queryset
        assert (
            "category IN ['electronics', 'books']"
            in queryset._IndexQuerySet__filters
        )

    @patch("meili.querysets.client")
    def test_filter_in_invalid_type(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)

        with pytest.raises(
            TypeError, match="Cannot compare <class 'str'> with list"
        ):
            queryset.filter(category__in="invalid")

    @patch("meili.querysets.client")
    def test_filter_range_list(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(price__range=[10, 100])

        assert result == queryset
        assert "price 10 TO 100" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_range_tuple(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(price__range=(5, 50))

        assert result == queryset
        assert "price 5 TO 50" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_range_range_object(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(price__range=range(1, 10))

        assert result == queryset
        assert "price 1 TO 10" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_range_invalid_type(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)

        with pytest.raises(
            TypeError,
            match="Cannot compare <class 'str'> with range, list or tuple",
        ):
            queryset.filter(price__range="invalid")

    @patch("meili.querysets.client")
    def test_filter_exists_true(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(field__exists=True)

        assert result == queryset
        assert "field EXISTS" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_exists_false(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(field__exists=False)

        assert result == queryset
        assert "field NOT EXISTS" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_exists_invalid_type(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)

        with pytest.raises(
            TypeError, match="Cannot compare <class 'str'> with bool"
        ):
            queryset.filter(field__exists="invalid")

    @patch("meili.querysets.client")
    def test_filter_isnull_true(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(field__isnull=True)

        assert result == queryset
        assert "field IS NULL" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_isnull_false(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        result = queryset.filter(field__isnull=False)

        assert result == queryset
        assert "field NOT IS NULL" in queryset._IndexQuerySet__filters

    @patch("meili.querysets.client")
    def test_filter_isnull_invalid_type(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)

        with pytest.raises(
            TypeError, match="Cannot compare <class 'str'> with bool"
        ):
            queryset.filter(field__isnull="invalid")

    @patch("meili.querysets.client")
    def test_filter_geo_radius(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockGeoModel)
        radius = Radius(lat=48.8566, lng=2.3522, radius=1000)
        result = queryset.filter(radius)

        assert result == queryset
        assert (
            "_geoRadius(48.8566, 2.3522, 1000)"
            in queryset._IndexQuerySet__filters
        )

    @patch("meili.querysets.client")
    def test_filter_geo_bounding_box(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockGeoModel)
        bbox = BoundingBox(top_right=(48.9, 2.4), bottom_left=(48.8, 2.3))
        result = queryset.filter(bbox)

        assert result == queryset
        assert (
            "_geoBoundingBox([48.9, 2.4], [48.8, 2.3])"
            in queryset._IndexQuerySet__filters
        )

    @patch("meili.querysets.client")
    def test_filter_geo_unsupported_model(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockModel)
        radius = Radius(lat=48.8566, lng=2.3522, radius=1000)

        with pytest.raises(
            TypeError, match="Model MockModel does not support geo filters"
        ):
            queryset.filter(radius)

    @patch("meili.querysets.client")
    def test_filter_geo_invalid_type(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        queryset = IndexQuerySet(MockGeoModel)

        with pytest.raises(
            TypeError,
            match="Unnamed Argument must be of type Radius or BoundingBox",
        ):
            queryset.filter("invalid_geo_filter")

    @patch("meili.querysets.client")
    def test_search_basic(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        mock_search_results = {
            "hits": [
                {"id": 1, "title": "Test Item 1"},
                {"id": 2, "title": "Test Item 2"},
            ],
            "estimatedTotalHits": 2,
        }
        self.mock_index.search.return_value = mock_search_results

        mock_obj1 = MagicMock()
        mock_obj1.pk = 1
        mock_obj2 = MagicMock()
        mock_obj2.pk = 2
        MockModel.objects.filter.return_value = [mock_obj1, mock_obj2]

        queryset = IndexQuerySet(MockModel)
        results = queryset.search("test query")

        expected_search_params = {
            "filter": [],
            "facets": [],
            "offset": 0,
            "limit": 20,
            "attributesToRetrieve": ["*"],
            "attributesToCrop": [],
            "cropLength": 10,
            "cropMarker": "...",
            "attributesToHighlight": ["*"],
            "highlightPreTag": "<mark>",
            "highlightPostTag": "</mark>",
            "showMatchesPosition": False,
            "sort": [],
            "matchingStrategy": "last",
            "showRankingScore": False,
            "attributesToSearchOn": ["*"],
            "locales": [],
        }
        self.mock_index.search.assert_called_once_with(
            "test query", expected_search_params
        )

        assert "results" in results
        assert "estimated_total_hits" in results
        assert "offset" in results
        assert "limit" in results
        assert results["estimated_total_hits"] == 2
        assert results["offset"] == 0
        assert results["limit"] == 20
        assert len(results["results"]) == 2

    @patch("meili.querysets.client")
    def test_search_empty_query(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        mock_search_results = {"hits": [], "estimatedTotalHits": 0}
        self.mock_index.search.return_value = mock_search_results
        MockModel.objects.filter.return_value = []

        queryset = IndexQuerySet(MockModel)
        _ = queryset.search()

        self.mock_index.search.assert_called_once()
        call_args = self.mock_index.search.call_args
        assert call_args[0][0] == ""

    @patch("meili.querysets.client")
    def test_search_with_filters_and_options(self, mock_client):
        mock_client.get_index.return_value = self.mock_index

        mock_search_results = {
            "hits": [
                {
                    "id": 1,
                    "_formatted": {"title": "<mark>test</mark>"},
                    "_rankingScore": 0.95,
                }
            ],
            "estimatedTotalHits": 1,
        }
        self.mock_index.search.return_value = mock_search_results

        mock_obj = MagicMock()
        mock_obj.pk = 1
        MockModel.objects.filter.return_value = [mock_obj]

        queryset = IndexQuerySet(MockModel)
        queryset.filter(category="electronics", price__gte=100)
        queryset.order_by("-created_at")
        queryset.paginate(limit=10, offset=5)
        queryset.matching_strategy("all")

        results = queryset.search("test")

        call_args = self.mock_index.search.call_args[0][1]
        assert "category = 'electronics'" in call_args["filter"]
        assert "price >= 100" in call_args["filter"]
        assert call_args["sort"] == ["created_at:desc"]
        assert call_args["limit"] == 10
        assert call_args["offset"] == 5
        assert call_args["matchingStrategy"] == "all"

        enriched_result = results["results"][0]
        assert "object" in enriched_result
        assert "_formatted" in enriched_result
        assert "_rankingScore" in enriched_result


class TestNamedTuples:
    def test_radius_creation(self):
        radius = Radius(lat=48.8566, lng=2.3522, radius=1000)

        assert radius.lat == 48.8566
        assert radius.lng == 2.3522
        assert radius.radius == 1000

    def test_radius_with_string_coords(self):
        radius = Radius(lat="48.8566", lng="2.3522", radius=1000)

        assert radius.lat == "48.8566"
        assert radius.lng == "2.3522"
        assert radius.radius == 1000

    def test_bounding_box_creation(self):
        bbox = BoundingBox(top_right=(48.9, 2.4), bottom_left=(48.8, 2.3))

        assert bbox.top_right == (48.9, 2.4)
        assert bbox.bottom_left == (48.8, 2.3)

    def test_bounding_box_with_string_coords(self):
        bbox = BoundingBox(
            top_right=("48.9", "2.4"), bottom_left=("48.8", "2.3")
        )

        assert bbox.top_right == ("48.9", "2.4")
        assert bbox.bottom_left == ("48.8", "2.3")

    def test_point_creation(self):
        point = Point(lat=48.8566, lng=2.3522)

        assert point.lat == 48.8566
        assert point.lng == 2.3522

    def test_point_with_string_coords(self):
        point = Point(lat="48.8566", lng="2.3522")

        assert point.lat == "48.8566"
        assert point.lng == "2.3522"
