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

    def count(self):
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
            match geo_filter:
                case Radius(lat=lat, lng=lng, radius=radius):
                    self.__filters.append(f"_geoRadius({lat}, {lng}, {radius})")
                case BoundingBox(top_right=top_right, bottom_left=bottom_left):
                    self.__filters.append(
                        f"_geoBoundingBox([{top_right[0]}, {top_right[1]}], "
                        f"[{bottom_left[0]}, {bottom_left[1]}])"
                    )
                case _:
                    raise TypeError(
                        f"Geo filter must be Radius or BoundingBox, not {type(geo_filter)}"
                    )

    def _apply_regular_filters(self, **filters):
        for full_lookup, value in filters.items():
            if "__" in full_lookup:
                field, lookup = full_lookup.split("__", 1)
            else:
                field, lookup = full_lookup, "exact"

            filter_expr = self._build_filter_expression(field, lookup, value)
            self.__filters.append(filter_expr)

    def _build_filter_expression(self, field: str, lookup: str, value) -> str:  # noqa: PLR0911, PLR0912
        match lookup:
            case "exact":
                if (
                    value == ""
                    or (isinstance(value, list) and len(value) == 0)
                    or value == {}
                ):
                    return f"{field} IS EMPTY"
                elif value is None:
                    return f"{field} IS NULL"
                elif isinstance(value, str):
                    return f"{field} = '{value}'"
                else:
                    return f"{field} = {value}"

            case "gte":
                if not isinstance(value, (int, float)):
                    raise TypeError(
                        f"Cannot compare {type(value)} with int or float"
                    )
                return f"{field} >= {value}"

            case "gt":
                if not isinstance(value, (int, float)):
                    raise TypeError(
                        f"Cannot compare {type(value)} with int or float"
                    )
                return f"{field} > {value}"

            case "lte":
                if not isinstance(value, (int, float)):
                    raise TypeError(
                        f"Cannot compare {type(value)} with int or float"
                    )
                return f"{field} <= {value}"

            case "lt":
                if not isinstance(value, (int, float)):
                    raise TypeError(
                        f"Cannot compare {type(value)} with int or float"
                    )
                return f"{field} < {value}"

            case "in":
                if not isinstance(value, list):
                    raise TypeError(f"Cannot compare {type(value)} with list")
                return f"{field} IN {value}"

            case "range":
                if not isinstance(value, (range, list, tuple)):
                    raise TypeError(
                        f"Cannot compare {type(value)} with range, list or tuple"
                    )
                if isinstance(value, range):
                    return f"{field} {value.start} TO {value.stop}"
                else:
                    return f"{field} {value[0]} TO {value[1]}"

            case "exists":
                if not isinstance(value, bool):
                    raise TypeError(f"Cannot compare {type(value)} with bool")
                return f"{field} {'NOT ' if not value else ''}EXISTS"

            case "isnull":
                if not isinstance(value, bool):
                    raise TypeError(f"Cannot compare {type(value)} with bool")
                return f"{field} {'NOT ' if not value else ''}IS NULL"

            case _:
                raise ValueError(f"Unsupported filter lookup: {lookup}")

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

        hits = results["hits"]
        filtered_objects = self.model.objects.filter(
            pk__in=[hit["id"] for hit in hits]
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
