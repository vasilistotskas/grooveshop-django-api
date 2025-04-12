from typing import TYPE_CHECKING, Literal, NamedTuple

from meili._client import client

if TYPE_CHECKING:
    from .models import IndexMixin


class Radius(NamedTuple):
    lat: float | str
    lng: float | str
    radius: int


class BoundingBox(NamedTuple):
    top_right: tuple[float | str, float | str]
    bottom_left: tuple[float | str, float | str]


class Point(NamedTuple):
    lat: float | str
    lng: float | str


class IndexQuerySet:
    def __init__(self, model: type["IndexMixin"]):
        self.model = model
        self.index = client.get_index(model._meilisearch["index_name"])
        self.__offset = 0
        self.__limit = 20
        self.__filters: list[str] = []
        self.__facets: list[str] = []
        self.__attributes_to_retrieve: list[str] = ["*"]
        self.__attributes_to_crop: list[str] = []
        self.__crop_length: int = 10
        self.__crop_marker: str = "..."
        self.__attributes_to_highlight: list[str] = ["*"]
        self.__highlight_pre_tag: str = "<mark>"
        self.__highlight_post_tag: str = "</mark>"
        self.__show_matches_position: bool = False
        self.__sort: list[str] = []
        self.__matching_strategy: Literal["last", "all"] = "last"
        self.__show_ranking_score: bool = False
        self.__attributes_to_search_on: list[str] = ["*"]
        self.__locales: list[str] = []

    def __repr__(self):
        return f"<IndexQuerySet for {self.model.__name__}>"

    def __str__(self):
        return f"IndexQuerySet for {self.model.__name__}"

    def __getitem__(self, index):
        if isinstance(index, slice):
            self.__offset = index.start
            self.__limit = index.stop
            return self
        else:
            raise TypeError("IndexQuerySet indices must be slices")

    def count(self) -> int:
        return self.index.get_stats().number_of_documents

    def paginate(self, limit: int, offset: int):
        self.__limit = limit
        self.__offset = offset
        return self

    def order_by(self, *fields: str):
        for field in fields:
            geopoint = "_" if "geoPoint" in field else ""
            if field.startswith("-"):
                self.__sort.append(f"{geopoint}{field[1:]}:desc")
            else:
                self.__sort.append(f"{geopoint}{field}:asc")
        return self

    def filter(self, *geo_filters, **filters):
        self._apply_geo_filters(*geo_filters)
        self._apply_regular_filters(**filters)
        return self

    def _apply_geo_filters(self, *geo_filters):
        for geo_filter in geo_filters:
            if not self.model._meilisearch["supports_geo"]:
                raise TypeError(
                    f"Model {self.model.__name__} does not support geo filters"
                )
            if not isinstance(geo_filter, Radius | BoundingBox):
                raise TypeError(
                    f"Unnamed Argument must be of type Radius or BoundingBox, not {type(geo_filter)}"
                )
            if isinstance(geo_filter, Radius):
                self.__filters.append(
                    f"_geoRadius({geo_filter.lat}, {geo_filter.lng}, {geo_filter.radius})"
                )
            elif isinstance(geo_filter, BoundingBox):
                self.__filters.append(
                    f"_geoBoundingBox([{geo_filter.top_right[0]}, {geo_filter.top_right[1]}], [{geo_filter.bottom_left[0]}, {geo_filter.bottom_left[1]}])"
                )
        return self

    def _apply_regular_filters(self, **filters):
        for full_lookup, value in filters.items():
            if "__" not in full_lookup or "__exact" in full_lookup:
                if (
                    value == ""
                    or (isinstance(value, list) and len(value) == 0)
                    or value == {}
                ):
                    self.__filters.append(
                        f"{full_lookup.split('__')[0]} IS EMPTY"
                    )
                elif value is None:
                    self.__filters.append(
                        f"{full_lookup.split('__')[0]} IS NULL"
                    )
                else:
                    self.__filters.append(
                        f"{full_lookup.split('__')[0]} = '{value}'"
                        if isinstance(value, str)
                        else f"{full_lookup.split('__')[0]} = {value}"
                    )
            elif "__gte" in full_lookup:
                if not isinstance(value, int | float):
                    raise TypeError(
                        f"Cannot compare {type(value)} with int or float"
                    )
                self.__filters.append(
                    f"{full_lookup.split('__')[0]} >= {value}"
                )
            elif "__gt" in full_lookup:
                if not isinstance(value, int | float):
                    raise TypeError(
                        f"Cannot compare {type(value)} with int or float"
                    )
                self.__filters.append(f"{full_lookup.split('__')[0]} > {value}")
            elif "__lte" in full_lookup:
                if not isinstance(value, int | float):
                    raise TypeError(
                        f"Cannot compare {type(value)} with int or float"
                    )
                self.__filters.append(
                    f"{full_lookup.split('__')[0]} <= {value}"
                )
            elif "__lt" in full_lookup:
                if not isinstance(value, int | float):
                    raise TypeError(
                        f"Cannot compare {type(value)} with int or float"
                    )
                self.__filters.append(f"{full_lookup.split('__')[0]} < {value}")
            elif "__in" in full_lookup:
                if not isinstance(value, list):
                    raise TypeError(f"Cannot compare {type(value)} with list")
                self.__filters.append(
                    f"{full_lookup.split('__')[0]} IN {value}"
                )
            elif "__range" in full_lookup:
                if not isinstance(value, range | list | tuple):
                    raise TypeError(
                        f"Cannot compare {type(value)} with range, list or tuple"
                    )
                self.__filters.append(
                    f"{full_lookup.split('__')[0]} {value[0]} TO {value[1]}"
                    if not isinstance(value, range)
                    else f"{full_lookup.split('__')[0]} {value.start} TO {value.stop}"
                )
            elif "__exists" in full_lookup:
                if not isinstance(value, bool):
                    raise TypeError(f"Cannot compare {type(value)} with bool")
                self.__filters.append(
                    f"{full_lookup.split('__')[0]} {'NOT ' if not value else ''}EXISTS"
                )
            elif "__isnull" in full_lookup:
                if not isinstance(value, bool):
                    raise TypeError(f"Cannot compare {type(value)} with bool")
                self.__filters.append(
                    f"{full_lookup.split('__')[0]} {'NOT ' if not value else ''}IS NULL"
                )

    def matching_strategy(self, strategy: Literal["last", "all"]):
        self.__matching_strategy = strategy
        return self

    def attributes_to_search_on(self, *attributes):
        self.__attributes_to_search_on.append(*attributes)
        return self

    def search(self, q: str = ""):
        results = self.index.search(
            q,
            {
                "filter": self.__filters,
                "facets": self.__facets,
                "offset": self.__offset,
                "limit": self.__limit,
                "attributesToRetrieve": self.__attributes_to_retrieve,
                "attributesToCrop": self.__attributes_to_crop,
                "cropLength": self.__crop_length,
                "cropMarker": self.__crop_marker,
                "attributesToHighlight": self.__attributes_to_highlight,
                "highlightPreTag": self.__highlight_pre_tag,
                "highlightPostTag": self.__highlight_post_tag,
                "showMatchesPosition": self.__show_matches_position,
                "sort": self.__sort,
                "matchingStrategy": self.__matching_strategy,
                "showRankingScore": self.__show_ranking_score,
                "attributesToSearchOn": self.__attributes_to_search_on,
                "locales": self.__locales,
            },
        )

        id_field = getattr(self.model.MeiliMeta, "primary_key", "id")
        hits = results.get("hits", [])
        filtered_objects = self.model.objects.filter(
            pk__in=[hit[id_field] for hit in hits]
        )

        enriched_results = []
        for obj in filtered_objects:
            hit = next(hit for hit in hits if str(hit["id"]) == str(obj.pk))
            enriched_results.append(
                {
                    "object": obj,
                    "_formatted": hit.get("_formatted", {}),
                    "_matchesPosition": hit.get("_matchesPosition", {}),
                    "_rankingScore": hit.get("_rankingScore", None),
                }
            )

        return {
            "results": enriched_results,
            "estimated_total_hits": results["estimatedTotalHits"],
            "offset": self.__offset,
            "limit": self.__limit,
        }
